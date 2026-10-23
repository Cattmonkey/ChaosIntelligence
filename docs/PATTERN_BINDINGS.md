# 解构绑定与结构化智能体结果

结构模式用于 `let`、`var`、函数参数、循环、推导，以及 0.51 新增的批量赋值和资源 `as` 绑定。它们是解析器和解释器中的原生节点，共用 `match` 的纯结构匹配规则。智能体调用得到的普通 List、Record 或标签数据，和普通函数的返回值使用同一套绑定规则。

## 一次取值，完整匹配，一次绑定

```chaos example=pattern-record
let {result: [first, second, ..tail], ..metadata}: Record =
    {result: [20, 21, 22], source: "worker"};
[first, second, tail, metadata]
```

右侧表达式只计算一次，接着验证整份值的类型注解，在临时映射中完成全部匹配并检查当前层的名字冲突，最后一次性提交所有名字。深层不匹配或最后一个名字冲突都不会留下前面的绑定。右侧已发生的工具调用等效果不回滚；原子性只涵盖这次绑定。

列表模式默认要求长度一致，`..rest` 收集剩余 List，`.._` 丢弃剩余项。记录模式默认要求字段集合完全相同；允许多余字段时必须写 `..rest` 或 `.._`。记录键必须显式写成 `key: pattern`，没有 `{name}` 缩写。每个目标中的名字和记录键不可重复。

```chaos example=pattern-strict
try { let [x, y] = [1]; x + y }
catch error { error.code }
```

失配产生 `match_error`。`match` 仍尝试下一分支；声明、循环与推导中的绑定失配立即抛错，不把它解释为筛选条件。

模式中的字面量使用严格类型相等，`1` 与 `true` 不匹配。嵌套 `_` 是通配符；为兼容既有程序，裸 `let _ = value`、`for _ in values` 和对应推导仍将 `_` 当作普通名字。没有名字的结构模式（例如 `let [] = []`）可以作为结构断言。

## 可变叶子与类型注解

```chaos example=pattern-mutable
var [count, label]: List = [1, "ready"];
fn increment() { count = count + 1 }
increment();
label = "done";
[count, label]
```

`let` 的叶子只读，`var` 的每个叶子各自获得独立 Cell，闭包继续共享其捕获的 Cell。目标后的注解约束整份初始值；结构内部不支持逐叶类型注解，可变叶子的后续赋值契约为 `Any`。需要持久的逐变量契约时，应使用普通 `var count: Int = ...`。单名称声明的原有契约和执行顺序保持不变。

## 一次更新多个已有变量

```chaos example=state-swap
var left: Int = 20;
var right: Int = 22;
[left, right] = [right, left];
[left, right]
```

结果是 `[22,20]`。左侧的名字引用最近的已有可变绑定，不声明新变量。最外层必须是列表、记录或构造器模式；内部支持嵌套、字面量断言、`_` 和 `..rest`，重复名字仍是语法错误。例如 `{answer: result, ..metadata} = reply` 会更新 result 与 metadata 两个已有变量；`.._` 只忽略多余字段。

执行顺序是：右侧求值一次 → 完整结构匹配 → 找到所有可写 Cell 并检查各自原有类型契约 → 最后检查取消与预算 → 无挂起地提交全部写入。语句返回整份右侧值。匹配、名称、可写性或任一类型检查失败，不提交这一组目标写入；右侧已发生的状态修改仍保留，没有部分成功的目标组。

```chaos example=state-rollback
var count: Int = 1;
var status: String = "ready";
var attempts: Int = 0;
fn response() { attempts += 1; [99, 42] }
try { [count, status] = response() } catch error { null };
[count, status, attempts]
```

结果是 `[1,"ready",1]`。第二个目标的类型不匹配，因此 count 也不更新；右侧已经产生的效果不会撤销。不存在或只读目标同样在右侧执行后检查，与普通 `name = value` 一致。无目标的结构，例如 `[] = []`，可以作为形状断言。裸 `_ = value` 仍更新名为 `_` 的变量；只有结构内部的 `_` 才是通配符。

普通名称赋值和批量赋值都先计算右侧；增强赋值则先捕获并读取目标，见[增强赋值](AUGMENTED_ASSIGNMENT.md)。右侧可以 await、yield 或修改状态。批量匹配读取右侧完成后的环境与构造器别名，而非右侧执行前的快照：

```chaos example=state-current-constructor
 data First { A(value) }
 data Second { B(value) }
 var Constructor = A;
 var answer = 0;
 fn response() { Constructor = B; B(42) }
 Constructor(answer) = response();
 answer
```

结果是 42。全部构造器解析与匹配发生在写回前；同一目标中前面的叶子不会改变后面构造器的查找结果。各叶保留原 Cell 身份及原契约，已捕获它们的闭包能读取更新后的值。

原子性只覆盖同一解释器中的这一组词法 Cell 写入。等待右侧时其他 Task 仍可修改状态；它不是跨整个右侧的锁、持久事务或外部效果回滚。模式不会展开用户迭代器、调用属性或条目 setter。对象成员和条目赋值继续使用其现有语法和协议，不能作为结构模式叶子；没有连锁赋值或解构增强赋值。异步函数的调用结果仍是冷 Task，批量赋值不会隐式等待叶子。

## 循环、推导与异步

```chaos example=pattern-loop
var total = 0;
for [left, right] in [[10, 11], [20, 21]] {
    total = total + left + right;
}
total
```

```chaos example=pattern-comprehension
[role + ": " + text
 for {role: role, result: {text: text}, .._} in [
     {role: "planner", result: {text: "plan"}, trace: 1},
     {role: "worker", result: {text: "done"}, trace: 2}
 ]]
```

每一项都在独立的词法环境中完整绑定，再执行条件、循环体或投影；闭包捕获自己的那一项。来源表达式不能看见自己尚未建立的目标绑定，后续子句可看见前面的绑定，投影可看见所有绑定。数据不匹配时，当前项已经消费，但条件、投影和循环体都不会执行。借用的来源不会被隐式关闭。

```chaos example=pattern-async
async fn* rows() { yield ([20, 21]); yield ([21, 22]) }
let totals = async (a + b async for [a, b] in rows());
[(await anext(totals)).value, (await anext(totals)).value,
 (await anext(totals)).done]
```

同步／异步生成器表达式在创建时仅计算最外层来源；取项和匹配从首次恢复开始。关闭尚未开始的包装器不会消费来源。取消、异常、预算和游标归属沿用既有[迭代](ITERATORS.md)及[挂起协议](EFFECTS.md)。

## 资源进入结果中的结构

```chaos example=state-resource
var events = [];
class Resource {
  fn __enter__(self) { events = append(events, "enter"); {pair: [20, 22]} }
  fn __exit__(self, error) { events = append(events, "exit"); null }
}
let result = with Resource() as {pair: [left, right]} { left + right };
[result, events]
```

结果为 `[42,["enter","exit"]]`。`with`／`async with` 支持与批量赋值相同的结构根；区别在于资源叶子是该作用域内的新只读绑定。进入方法执行一次，完整匹配后才发布叶子，构造器在本项绑定之前的外层环境查找。多项资源可混合普通名称与结构，后项管理器可以使用前项叶子；每项都有自己的词法作用域。

```chaos example=state-resource-failure
var errors = [];
class Resource {
  fn __enter__(self) { [1] }
  fn __exit__(self, error) {
    errors = append(errors, error.code);
    true
  }
}
let result = with Resource() as [left, right] { "unreachable" };
[result, errors]
```

结果为 `[null,["match_error"]]`。进入已经成功，因此解构失败仍调用保存的退出协议；它可按普通错误规则抑制失配。失败的模式不发布任何叶子，不执行主体或后续资源，已进入的外层资源按正常逆序退出。进入自身失败时仍不调用本项退出方法。

```chaos example=state-resource-async
import "std/context" as Context;
var events = [];
async fn* acquire() {
  events = append(events, "enter");
  try { yield {pair: [20, 22]} }
  finally { events = append(events, "exit") }
}
let resource = Context.asynccontextmanager(acquire);
let result = async with resource() as {pair: [left, right]} { left + right };
[result, events]
```

结果同样为 `[42,["enter","exit"]]`。生成器资源、ExitStack、Computer、TaskGroup 和 CancelShield 沿用原生命周期，不被模式绑定替换。模式只匹配原生结构；不能借 Record 模式读取这些句柄的属性，因此不适配的模式会按既有控制规则结束已进入的作用域。TaskGroup 的绑定失败属于组体失败；CancelShield 在其保护范围内绑定并正常释放保护。硬取消与预算错误仍遵守[资源控制规则](CONTEXTS.md)，不承诺执行任意用户清理。

## 标签数据与构造器作用域

```chaos example=pattern-tag
 data Result { Ok(value), Failed(reason) }
 let Ok({text: answer}) = Ok({text: "ready"});
 answer
```

支持已有的限定构造器模式，例如 `Module.Ok(value)`。一次目标中的所有构造器都在绑定前的外层环境中解析，前面出现的新名字不会改变后面构造器的身份。构造器必须属于当前可用的数据类型且参数数目正确；这类使用错误与普通形状失配区别处理。

匹配仅检查原生结构与标签数据，不会调用用户 `__iter__`、属性 getter、模型或工具。遍历来源可以实现迭代协议，但每一项的结构匹配本身不会执行任意用户代码。

## 宏与显式构造器注入

```chaos example=pattern-macro
macro sums(rows) { quote { [a + b for [a, b] in unquote(rows)] } }
let a = 100;
[sums!([[20, 21], [21, 22]]), a]
```

卫生宏为每个新名字分配独立身份，右侧及构造器引用保持绑定前作用域，`unquote` 保留调用者身份。编译期纯求值支持 `let` 的结构绑定和同步 List／Record 推导中的结构目标；可变状态、标签构造器、异步与运行时效果仍不能在纯编译期执行。

```chaos example=pattern-macro-constructor
 data Box { Boxed(value) }
 macro unwrap(constructor, value) {
     quote {
         do {
             let C = unquote(constructor);
             let C(item) = unquote(value);
             item
         }
     }
 }
 unwrap!(Boxed, Boxed(42))
```

模板内使用构造器时，需要像其他自由值一样显式注入，并先绑定一个模板内别名。相同规则也适用于模板内的 `match`；宏不能隐式捕获调用方同名构造器。

模板内的批量赋值复用已有卫生变量身份，不新建同名绑定。自由赋值目标必须显式来自调用者 Syntax；可注入整个 `do { ... }` 赋值块，当前不新增模式内部的 unquote 拼接语法。资源结构目标则和其他声明一样获得新身份，调用者插入的主体继续引用调用者自己的变量。

```chaos example=state-macro
macro exchange() {
  quote {
    var [left, right] = [20, 22];
    [left, right] = [right, left];
    [left, right]
  }
}
let left = 100;
[exchange!(), left]
```

结果为 `[[22,20],100]`。宏纯求值阶段仍不能执行批量赋值或资源作用域；quote 生成的这些操作在运行期执行。

## 模块、结构编辑和 IDE

模块可以导出 `let` 结构目标中的各个名字；`var` 结构目标中的所有名字仍属于不可直接导出的可变状态。函数参数可以使用下节的 `name as pattern`，保留既有关键字标签、默认值与契约规则。类体中的字段声明仍要求单一公共成员名，方法体可以解构。已有变量可批量解构赋值，资源 `as` 可直接解构；0.56 增加 `C::{field: pattern}` 类字段模式，见[类字段模式](CLASS_PATTERNS.md)。无名称槽的裸参数模式和结构内部成员／索引写入仍未实现。

Program 新增 `let_pattern`、`var_pattern`、`for_pattern`、`async_for_pattern`、`comp_for_pattern`、`comp_async_for_pattern`，目标复用已有 `p_*` 模式节点。0.51 加入 `assign_pattern(pattern,value)`、`with_pattern(pattern,manager,body)`、`async_with_pattern(pattern,manager,body)`，三个节点的 pattern 根限定为 p_list／p_tuple／p_record／p_tag／p_class。旧单名称节点及 `chaos.program/1`、languageVersion 1／2 均保留。

IDE 的语义高亮、悬浮、补全和定义定位从这些节点提取真实绑定；赋值叶子是已有符号的写引用，资源叶子是局部声明；对未知智能体结果保守显示 `Any`，不执行智能体来猜测类型。源码格式化保留字符串和注释，并重新解析验证 AST 等价。源码、结构编辑、CLI、SDK、HTTP 与在线文档使用同一语言实现。

参见[语言参考](LANGUAGE.md)、[推导式](COMPREHENSIONS.md)、[宏](MACROS.md)、[IDE](IDE.md)和[开发者说明](DEVELOPER.md)。

## 函数参数中的模式

0.38 把相同的结构匹配协议用于函数调用边界。语法是 `name as pattern: Type = default`，类型和默认值仍可省略。公开参数名 `name` 接收整份值，模式的每个叶子另外成为函数内只读绑定。

```chaos example=parameter-pattern-record
fn sum(result as {pair: [left, right], ..metadata}: Record) {
    [left + right, metadata, result.pair]
}
sum(result={pair: [20, 22], source: "worker"})
```

关键字标签仍为 `result`，`left`、`right`、`metadata` 不是可独立传入的参数。普通位置参数、仅关键字参数、`*rest` 和 `**options` 都能声明模式；没有增加隐式位置专用参数或裸 `fn([a, b])` 语法。

```chaos example=parameter-pattern-rest
fn pack(*values as [first, ..tail]: List,
        scale: Int = 1,
        **options as {label: label, .._}: Record) {
    [first * scale, tail, label, options.label]
}
pack(20, 22, scale=2, label="sum")
```

调用先求出全部实参并执行已有的位置、名称、默认值和整值类型检查，再在临时映射中匹配各参数。全部成功后才创建可执行函数体的词法绑定；任何失配抛 `match_error`，函数体不会运行。实参求值中已经发生的效果不会撤销。默认表达式仍在定义时求值一次并检查整值契约；模式在实际调用时检查，包括省略参数而使用默认值的调用。

所有模式构造器从同一函数捕获环境查找，不从本次参数或前面匹配出的叶子查找。捕获环境沿用闭包规则：可变 Cell 在每次调用时读取当前值，不额外冻结定义时的构造器身份。冷句柄保存创建时成功匹配的绑定，恢复时不会重做匹配。所有公开参数名与全部模式叶子必须互不重复，重复是解析错误。`_` 在模式内是通配符，作为公开参数名时保持普通名称语义。模式不调用用户迭代、属性 getter、模型或工具；叶子不拥有隐含权限。

```chaos example=parameter-pattern-cold
var calls = 0;
async fn add(pair as [left, right]: List) {
    calls = calls + 1;
    left + right
}
let error = try { add([1]); "unexpected" } catch e { e.code };
[error, calls, await add([20, 22])]
```

异步函数及同步／异步生成器也在创建冷 Task／流之前验证模式，不把参数错误藏到首次恢复。匿名函数、闭包、方法、`apply`、`partial`、`map`、管道及尾调用走同一入口。类实例通过显式 `C::{field: pattern}` 匹配有效存储字段；不会隐式展开成 Record，也不会执行 getter 或用户协议。

```chaos example=parameter-pattern-macro
macro make_adder() { quote { fn(pair as [left, right]: List) { left + right } } }
let left = 100;
let add = make_adder!();
[add(pair=[20, 22]), left]
```

卫生宏保留公开关键字标签，为参数槽的内部身份和各模式叶子分别重命名。默认值与模式构造器仍使用模板的外层词法范围。纯编译期函数可使用原生结构参数模式，但不能执行标签构造器或运行时效果。

普通智能体返回值与工具返回值无需转换成另一种工作流协议；直接传给这样的函数即可。模式只约束输入结构，不证明模型答案正确。下面函数执行前会验证智能体结果，实际调用 `author` 需要配置相应提供方：

```chaos
fn consume(reply as {task: {value: value}, trace: trace}: Record) {
    {answer: value + 1, trace: trace}
}
// author 是普通 Agent 可调用值；仅其调用执行模型效果。
consume(author(input))
```

函数 Program IR 在原参数字段上增加可选 `pattern`，其值是既有 `p_*` 节点。旧 `{name, type}` 及 `{name, type, mode, default}` 仍保持原样。IDE 以相同模式节点提供叶子着色、作用域提示和定义位置；叶子的保守提示不等于完整静态推导。格式化保持注释、字符串和模式结构，保存／可视结构编辑／无界面执行使用同一解析器。

可下载并通过 `chaos run` 执行[结构参数示例](../examples/parameter-patterns.chaos)。

## 元组模式

0.53 增加独立的 `(left,right)`、`(value,)`、`(first,..rest)` 模式；剩余部分保持 Tuple 类型。它与 List 模式严格区分，可用于本页全部既有声明、参数、循环、推导、资源和原子赋值位置。匹配仍不调用用户协议；既有契约预检、词法身份和资源清理规则不变。见[元组规范](TUPLES.md#所有结构绑定位置)。

## 挂起与执行状态

结构声明／赋值的右值以及 match 的被匹配值、守卫、主体可沿原语言调用正常挂起，其待决位置现在保存在一个真实模式帧中；恢复不会重算前面的效果。声明先右值、再整体契约和模式预检，可变叶子仍为独立 Any Cell；赋值先核对全部现有 Cell 与契约，再一次提交，右值效果不回滚。参数、循环、推导、资源和 catch 继续共享原纯模式算法，未新增协议调用或隐式 await。见[执行状态与完整边界](EXECUTION_FRAMES.md#h-模式分支与结构绑定的执行状态)。

## 模式结构实现：直接模式结构的 CFG

模式结构实现将直接选中的 match、let_pattern、var_pattern 与 assign_pattern 接入 auto-flow／native-flow 的同一结构 CFG，适用于四种原合法函数形式。后端实际选择被匹配值或右值、有序守卫及所选主体；守卫仍先由原运行时检查具体 Bool，false 后继续原有序匹配并释放该分支的绑定范围。PatternSyntaxFrame、PatternCloseCursor、纯匹配器与原子提交算法保持原实现，未新增算法帧或公开查询字段。

声明仍按右值、整体契约、模式及名称预检的原顺序执行；赋值先核对所有现有 Cell 与契约，经过原最后检查点后整组提交，右值效果不回滚。选中 match 主体保留原尾位置及零／多返回值，不另加保护值归约。原生关闭沿原委托完成或异常前沿推进；借用任务／流、权限、收费根、TaskGroup 独立组体游标及清理优先级保持原合同，不重放工具、模型或 Robot 动作。

这扩展了已有语言功能的编译覆盖，不把嵌在未选中的绑定、调用或容器宿主子树自动拆成 CFG。原有 AST 节点／深度及指令／标志配额继续限制准入：auto-flow 可按原规则回退，native-flow 明确拒绝不支持的计划。资源、保护及模式语义事件的效果元数据保守包含原 AST 子树的效果标签；未知调用仍是屏障，不能按函数名字推断纯度或据此重排执行。
