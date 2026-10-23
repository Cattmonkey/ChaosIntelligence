# 泛型函数与方法组合

泛型函数是原生一等 Function：同一个调用按多个参数的实际类型或显式值特化选择实现。方法可以是普通函数、闭包或智能体调用的包装函数，仍进入原来的 AST、调用栈、能力检查与任务调度器。名称用于说明身份，不参与分派；类型不是通过提示词、类名字或消息文本猜出来的。

完整 before／primary／after 组合的待决位置、方法、结果和范围由 GenericResolveFrame 保存；纯选择与 next 描述构造仍为同步辅助。原方法表、调用形状、冷结果、异常和权限合同不变。详见[真实执行状态](EXECUTION_FRAMES.md#函数形成与泛型方法组合的执行状态)。

## 创建与持久扩展

```chaos example=generic-persistent
import "std/generic" as G;
let empty = G.generic_function("resolve");
let base = G.generic_method(empty, ["Any"], fn(value) { "fallback" });
let integers = G.generic_method(base, ["Int"], fn(value) { value + 1 });
let removed = G.generic_remove(integers, ["Int"]);
[base(41), integers(41), removed(41), type_of(integers),
 G.generic_info(base).methodCount, G.generic_info(integers).methodCount]
```

结果为 `["fallback",42,"fallback","Function",1,2]`。这里的“持久”指不可变方法表：新增、替换、删除都返回新 Function，已有值保持原行为，不是把活闭包写入磁盘。保存程序仍使用源码／Program 版本；另一个运行期不能从 JSON 描述恢复活函数。

| 接口 | 行为 |
| --- | --- |
| `generic_function(name, arity=1)` | 创建空泛型函数；name 为1..160字符 String，arity 为 1..32 的 Int |
| `generic_method(function, specializers, implementation, qualifier="primary", *, with_next=false)` | 返回新版本；相同特化键序列和 qualifier 替换原方法；每个版本最多128个方法；with_next 是严格 Bool、仅关键字参数，只能在 primary 上启用 |
| `generic_remove(function, specializers, qualifier="primary")` | 返回移除指定方法后的版本；不存在时无害地保留方法表，不保证返回原对象身份 |
| `generic_methods(function)` | 返回方法记录 List，普通项为 specializers、implementation、qualifier；仅显式启用 next 的 primary 额外含 withNext:true；保留真实类型与可调用值，并按当前能力投影；投影后的函数对象身份可能不同 |
| `generic_info(function)` | 返回 name:String、arity:Int、methodCount:Int、qualifiers:List、precedence:String、nextArguments:String |
| `generic_next_available(next)` | Bool；检查真实泛型接续是否还有下一方法，不执行方法；普通函数不是 next 令牌 |
| `generic_eql(value)` | 创建当前 Evaluator 所有的不可变 EqlSpecializer，保留真实语言值的比较身份 |
| `generic_eql_info(specializer)` | 返回 kind、comparison、valueType 三个 String 字段；只诊断，不返回被捕获的值 |

八个函数都作为全局内建和 `std/generic` 的同名导出提供。查询不会试调用实现。`generic_info` 的 precedence 固定为 `"left-to-right"`，nextArguments 固定为 `"captured"`。它没有宣称静态返回类型，也不生成虚构的程序哈希。

## 实际类型与多参数选择

specializers 必须是长度恰好等于 arity 的 List。每项是实际 Class、实际 DataType、generic_eql 创建的 EqlSpecializer，或内建契约名称字符串，例如 `"Int"`、`"String"`、`"Any"`；用户类请传类值本身。[组合 Contract](CONTRACTS.md)不作为 specializer，当前没有为结构契约定义分派优先级；内建字符串 `"Contract"` 可以特化描述值本身。两个同名类或独立 DataType 不因此成为同一个方法键。DataType 的值匹配仍遵循原有标签记录的结构契约；身份只用于方法表键，不把标签数据改成名义实例。两个独立 DataType 若同时接受同一结构值且没有优先关系，会产生歧义。子类按其实际 C3 MRO 排序；类型检查不调用用户的 `__instancecheck__` 等元类钩子。

调用必须提供至少 arity 个位置参数；分派只读取这些位置，剩余位置参数和全部具名参数原样转交实现。只用具名参数不能补足分派位置。实现自己的默认值、位置专用参数、契约和具名绑定继续由普通调用协议检查。

```chaos example=generic-precedence
class Base {}
class Specific(Base) {}
let pair = generic_function("pair", 2);
let first = generic_method(pair, [Specific, Base], fn(a, b) { "left" });
let both = generic_method(first, [Base, Specific], fn(a, b) { "right" });
both(Specific(), Specific())
```

结果为 `"left"`。比较适用方法时从左到右，首次出现的具体程度差异决定优先级；后一个参数不能推翻前一个参数的选择。Class 实例使用类的真实 C3 MRO；Class 值本身使用其实际元类的 C3 MRO，内建 `"Class"` 契约也可选择 Class 值。实际名义类型特化优先于适用的内建类型契约。

内建契约使用明确的子类型关系；两个同时适用但无法排序的契约会报 `ambiguous_method`，不会按注册顺序或名字选一个。调用前必须存在适用的 primary；即使 around 准备直接返回，没有适用主方法仍报 `no_applicable_method`。所有可达组合顺序在执行方法前确定，歧义会先于方法效果报错。这套有限分派不提供任意谓词特化、自定义参数优先级或完整 CLOS 元对象协议。

`"Number"` 与 `"Rational"` 都接受 Int，但彼此不包含；可以增加明确的 `"Int"` 方法消除这一交集的歧义。

```chaos example=generic-overlap
var choose = generic_function("choose");
choose = generic_method(choose, ["Number"], fn(n) { "number" });
choose = generic_method(choose, ["Rational"], fn(n) { "rational" });
let overlap = try { choose(1) } catch error { error.code };
choose = generic_method(choose, ["Int"], fn(n) { "integer" });
[overlap, choose(1), choose(1.5), choose(fraction(1, 2))]
```

结果为 `["ambiguous_method","integer","number","rational"]`。

## 实际值特化

`generic_eql(value)` 在登记时接收已经求值的真实语言值。它不解析源码拼写、提示词或字段内容，也不是一个任意条件回调。匹配和方法键比较不会调用用户 `__eq__`、`__hash__`、属性 getter 或迭代器。

```chaos example=generic-eql-value
import "std/generic" as G;
let base = G.generic_method(G.generic_function("route"), ["String"], fn(value) { "ordinary" });
let review = G.generic_method(base, [G.generic_eql("review")], fn(next, value) { next() + ":review" }, with_next=true);
let replaced = G.generic_method(review, [G.generic_eql("re" + "view")], fn(value) { "replaced" });
let removed = G.generic_remove(replaced, [G.generic_eql("review")]);
[review("review"), review("other"), replaced("review"), removed("review"), G.generic_info(replaced).methodCount]
```

结果为 `["ordinary:review","ordinary","replaced","ordinary",2]`。同一位置上，适用的值特化优先于适用的类型特化；多参数仍按从左到右第一个不同位置比较，后面位置的值特化不能推翻前面位置已经确定的优先级。每次构造的包装器仍是不同原生对象，普通 `==` 按包装器身份比较；EqlSpecializer 本身不可哈希，不能作为 Set 成员或 HashMap 键。等价值构造出的多个包装器只在泛型方法键协议中表示相同位置，因此替换和移除不要求保存原包装器。不同 qualifier 仍是不同方法位置，旧方法表保持不变。

| 被捕获的值 | 比较规则 |
| --- | --- |
| Null、Bool、Int、Number、String、Fraction、Decimal | 先要求相同原生类型，再按纯值比较；true、1、1.0 是三个不同键 |
| Number 的正负零 | 按相同值比较 |
| Decimal | 按规范化数值比较，不同 scale 和正负零不产生新键；不改变当前 Decimal context 的 flags |
| Complex | 保留 Complex 类型；按实际构造后的实部与虚部类型及值比较，不与实数合并 |
| List、Record、Tuple、Set、HashMap、Class、Instance、Function、Agent 及其他合法语言值 | 按真实对象身份；两个内容相同的容器仍是不同键 |

```chaos example=generic-eql-identity
let token = {kind: "review"};
let base = generic_method(generic_function("identity"), ["Any"], fn(value) { "other" });
let selected = generic_method(base, [generic_eql(token)], fn(value) { "same" });
[selected(token), selected({kind: "review"}), generic_eql_info(generic_eql(token))]
```

结果为 `["same","other",{kind:"eql",comparison:"identity",valueType:"Record"}]`。身份特化不会冻结或复制整个对象；内部状态改变不改变其匹配身份。用 `generic_eql_info` 可以查询比较策略和原生类型，但不能读取隐藏 payload。诊断 JSON 形如 `{$eqlSpecializer:{kind:"eql",comparison:"identity",valueType:"Record"}}`；它不含宿主地址，也不能被反序列化成活特化值。

```chaos example=generic-eql-numeric
var choose = generic_method(generic_function("numeric"), ["Any"], fn(value) { "other" });
choose = generic_method(choose, [generic_eql(1)], fn(value) { "int" });
choose = generic_method(choose, [generic_eql(1.0)], fn(value) { "number" });
choose = generic_method(choose, [generic_eql(true)], fn(value) { "bool" });
[choose(1), choose(1.0), choose(true), generic_eql_info(generic_eql(1)).valueType]
```

结果为 `["int","number","bool","Int"]`。设计参考 [Common Lisp EQL](https://www.lispworks.com/documentation/HyperSpec/Body/f_eql.htm) 的对象身份／同型数值区分和 [方法优先级](https://www.lispworks.com/documentation/HyperSpec/Body/07_ffab.htm) 的值特化优先规则。Chaos 特意让 String 按值比较，并保留 Complex 的实际原生类型，不采用 Lisp reader 将零虚部有理复数约化为实数的行为；因此不是 Common Lisp 的逐项兼容实现。

值特化遵守既有所有权、任务 epoch、能力上限与捕获图预算。隐藏值也参加统一原生捕获图检查；包装或保存进方法表不产生新的权限，投影收窄包装器但保留匹配身份。返回的 EqlSpecializer 可用作普通命名类型注解；它只说明描述值的类型，不自动静态推导被选中方法的返回类型。值特化下的 primary next、around、before／after、冷任务和生成器仍使用下文同一组合规则。

## 组合调用而非任务完成

qualifier 可为 `primary`、`before`、`after`、`around`。最具体的 around 在最外层，收到 `next` 加本次完整原参数。它可直接返回，也可调用严格零参数的 `next()`，进入下一层 around 或核心组合。next 捕获本次方法表版本、实参与能力上限，不能传入替换参数。

核心组合依次调用最具体到最一般的 before、最具体的 primary，然后从最一般到最具体的 after。默认 primary、before、after 只收到原参数；primary 可通过 with_next=true 显式取得后续主方法接续，规则见下节。before／after 必须同步完成且返回 null。失败沿原错误和清理规则传播；after 不是 finally，不保证失败后执行，也不能代替资源管理器。

```chaos example=generic-combination
var log = [];
fn mark(label) { log = append(log, label); null }
var work = generic_function("work");
work = generic_method(work, ["Int"], fn(n) { mark("primary"); n + 1 });
work = generic_method(work, ["Any"], fn(n) { mark("before-any") }, "before");
work = generic_method(work, ["Int"], fn(n) { mark("before-int") }, "before");
work = generic_method(work, ["Any"], fn(n) { mark("after-any") }, "after");
work = generic_method(work, ["Int"], fn(n) { mark("after-int") }, "after");
work = generic_method(work, ["Any"], fn(next, n) {
  mark("around-enter");
  let result = next();
  mark("around-exit");
  result
}, "around");
[work(41), log]
```

结果为 `[42,["around-enter","before-int","before-any","primary","after-any","after-int","around-exit"]]`。方法可以有普通语言效果；已经发生的工具操作不会因后续方法失败而回滚。

```chaos example=generic-cold-task
var log = [];
async fn calculate(n) { log = append(log, "body"); n + 2 }
let primary = generic_method(generic_function("calculate"), ["Int"], calculate);
let work = generic_method(primary, ["Int"], fn(n) { log = append(log, "after"); null }, "after");
let pending = work(40);
let beforeAwait = log;
[type_of(pending), beforeAwait, await pending, log]
```

结果为 `["Task",["after"],42,["after","body"]]`。primary／around 返回的冷 Task、Generator 或 AsyncGenerator 保持原值和生命周期，不自动 await 或消费。after 在本次调用返回后、冷值完成前执行。要围绕完整异步工作清理资源，应在 async 实现内部使用 `try/finally` 或 `async with`。已知 async／生成器形式的 before／after 在登记时拒绝；经过尾调用才发现异步实现时，可在实际调用时报告 `argument_error`。实际返回冷句柄或非 null 值会报 `generic_protocol_error`，错误 value 保留实际返回值，调用方仍需按原规则处理它。

可直接运行[完整示例](../examples/generic-functions.chaos)、[primary next 示例](../examples/generic-next.chaos)及[值特化示例](../examples/generic-eql.chaos)，其方法表在普通源码中构造，也可经 SDK 加载。

## 主方法的显式 next

启用 `with_next=true` 的 primary 收到 `(next, *原位置参数, **原具名参数)`；没有启用的 primary 保留原调用签名。with_next 只能作为关键字传入，必须是严格 Bool；before／after／around 不能启用它。around 原来就接收 next，无需该开关。

```chaos example=generic-primary-next
import "std/generic" as G;
let base = G.generic_method(G.generic_function("calculate"), ["Number"], fn(value) { value + 1 });
let calculate = G.generic_method(base, ["Int"], fn(next, value) { next() + 1 }, with_next=true);
[base(40), calculate(40), G.generic_methods(calculate)[1].withNext]
```

结果为 `[41,42,true]`。next 严格接受零个参数，使用原来的方法表版本、全部实参和能力上限调用下一个较一般的 primary。它不会从头重新分派、重新进入 around 或重新执行 before／after。相同 specializers 与 qualifier 仍表示同一方法位置；替换时是否启用 next 由新登记决定，with_next 不增加一个新的方法键。

构建主方法链时使用原固定 C3／从左到右规则。只预先排序实际可达的后续 primary：遇到普通 primary 即停止，因为它没有继续调用主方法链的入口；该位置之后的潜在歧义不会改变旧方法的行为。可达后续的歧义则在任何方法效果前失败。

```chaos example=generic-primary-next-once
var log = [];
let base = generic_method(generic_function("twice"), ["Any"], fn(value) {
  log = append(log, "base"); value
});
let primary = generic_method(base, ["Int"], fn(next, value) {
  log = append(log, "primary"); next() + next()
}, with_next=true);
let before = generic_method(primary, ["Any"], fn(value) { log = append(log, "before"); null }, "before");
let work = generic_method(before, ["Any"], fn(value) { log = append(log, "after"); null }, "after");
[work(21), log]
```

结果为 `[42,["before","primary","base","base","after"]]`。重复 primary next 会重复执行后续主方法的普通效果，但同一次核心组合的 before／after 只执行一次。around next 保留原来可以再次进入核心组合的语义，因此不能把这个保证理解为任意重复 around 都只运行一次辅助方法。

```chaos example=generic-primary-next-snapshot
let base = generic_method(generic_function("saved"), ["Any"], fn(value) { value + 2 });
let work = generic_method(base, ["Int"], fn(next, value) { next }, with_next=true);
let saved = work(40);
let changed = generic_method(work, ["Any"], fn(value) { 100 });
let terminal = generic_method(generic_function("terminal"), ["Int"], fn(next, value) {
  [generic_next_available(next), try { next() } catch error { error.code }]
}, with_next=true);
[generic_next_available(saved), saved(), changed(40)(), terminal(0)]
```

结果为 `[true,42,100,[false,"no_next_method"]]`。保存的 primary next 只保存后续主方法链，不保存一个待重新执行的完整外层组合。它仍受当前解释器、值生命周期、资源预算和原有能力上限约束。generic_next_available 只说明结构上存在后续方法，不保证该方法的业务行为成功或工具权限可用；终端 next() 报 no_next_method。普通 Function 传给查询报 argument_error，跨解释器令牌报 ownership_error。外部 JSON 描述不能重建接续。

```chaos example=generic-primary-next-cold
var log = [];
let base = generic_method(generic_function("async-work"), ["Any"], async fn(value) {
  log = append(log, "base"); await sleep(0); value
});
let primary = generic_method(base, ["Int"], async fn(next, value) {
  log = append(log, "primary"); let result = await next(); result + 2
}, with_next=true);
let work = generic_method(primary, ["Any"], fn(value) { log = append(log, "after"); null }, "after");
let pending = work(40);
let beforeAwait = log;
[type_of(pending), beforeAwait, await pending, log]
```

结果为 `["Task",["after"],42,["after","primary","base"]]`。启用 next 不改变冷 Task／Generator 语义。异步主方法显式 await 下一方法返回的 Task；after 仍发生在最外层调用返回时，而不是异步工作完成时。生成器接续也不会被隐式消费。

## 与智能体及自编程组合

泛型函数可作为 AgentTool 实现或 Team 角色：类型或值分派挑选普通语言实现，再由该实现调用实际 Agent、Session 或 Computer。泛型函数没有自己的模型配置、权限系统或后台执行器。录制、取消、错误因果、能力收窄和当前任务上下文使用同一调用路径；保存 next 或方法记录不能恢复更高权限。next 的别名共享本次调用的能力上限；在实际调用的原生捕获图中传递或调用 next 时会进一步收窄，之后不能借另一个旧别名恢复原权限。检查涵盖语言 Function 保存的完整词法 Env／Cell／默认值、原生适配器显式声明的 captures、List／Record／Tuple／HashMap、实际类与实例存储、成员包装，以及已声明的工具处理器、Task／Generator 捕获根、活动求值的临时操作数与运行帧、通道、上下文、资源栈、任务组和恢复范围中的语言值；每条路径与已有能力上限求交。

尚未消费的已求值操作数也由解释器显式登记。例如，列表前面的元素或已经求值的调用参数，会在后续子表达式 await／yield 时留在相应逻辑 Task／Generator 的捕获根中；透明执行驱动共享该逻辑范围。语句、迭代和尾调用转换会清理或替换已经消费的临时根，操作完成及异常展开时退出对应范围，不保留无限增长的求值历史。每个活动求值范围最多保存20000个不同的临时对象根，超限沿原 ResourceLimit 传播；实际遍历仍受下述共享图预算约束。

这是对真实原生存储的保守遍历，不按函数正文猜测自由变量，也不执行用户 getter、哈希、相等或迭代协议；不检查 Python 闭包、宿主迭代器帧或任意 __dict__。因此，共享词法环境中实际保存的接续也可能一起被收窄，即使当前函数正文没有直接引用它。

每次捕获图检查最多消耗20000个节点步骤、100层深度；List／Record／Tuple 和 HashMap 单容器最多10000项，仍计入原执行预算。先完成整个图的所有权、生命周期与预算预检，再统一提交本次接续权限收窄；非法图不会部分改变共享令牌。业务方法开始后失败，则不撤销已经提交的权限收窄。受信任的宿主 Native.call 回调本身不在检查图内；只有适配器明确声明的语言值 captures 会参与检查。这不是宿主沙箱、任意对象图序列化或活栈恢复机制。

Program、Code／function_from 和 module_from 可以生成、导出和重建泛型函数定义；扩展返回新版本，已有闭包持有的旧函数不会被暗中替换。持续演进仍由候选测试和原 Program／Process 采用协议管理，不把瞬时方法表当成可跨进程恢复的运行栈。

源码、IR、卫生宏、格式化、IDE、LSP、SDK、CLI、HTTP 和便携嵌入包共享现有调用表达式，没有新的关键字或 IR 节点。IDE 从 NativeSpecs 获得八个接口签名、EqlSpecializer 类型和信息记录字段；泛型调用结果保守为 Any，不执行方法或按名字猜返回类型。完整 Python／Lisp 生态、CLOS 方法组合定制和通用 continuation 仍未完成。

没有待执行 after 的 primary／around 尾调用沿用原调用跳板；非尾 around 或 after 需要保留后续工作，其递归调用受普通调用深度与资源预算限制。这不是通用 continuation，也不承诺任意方法组合都能在常量栈空间递归。
