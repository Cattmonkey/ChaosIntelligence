# 组合契约与数据边界

Contract 是解释器所有的不可变契约描述值。它把已有 List、Tuple、Record、HashMap、联合与可空检查组合起来，并用局部符号引用声明单一递归或互递归数据，直接用于原有绑定、赋值、函数参数和返回值的命名注解。智能体的输入边界可以由语言函数自己声明，无需另建页面校验器或在提示词中猜测数据形状。

## 命名契约就是普通值

```chaos example=contracts-boundary
import "std/contracts" as C;
let Request = C.contract_record({values: C.contract_list("Int")});
fn solve(request: Request) -> Int { request.values[0] + 2 }
[solve({values: [40]}), C.contract_accepts(Request, {values: ["40"]}), type_of(Request)]
```

结果为 `[42,false,"Contract"]`。`Request` 是普通词法绑定，函数创建时捕获实际描述值；别名的拼写不影响检查语义。子契约可以是内建契约名字符串、真实 Class、DataType，或另一 Contract。字符串表示已经存在的内建契约，不解析 `"List[Int]"` 等新语法，也不按文本寻找同名用户类。

`Contract` 内建契约接受描述值本身；`request: Request` 接受 Request 描述的数据。二者不可互换。省略注解仍是 Any。运行时不会转换 `"40"` 为整数，也不会把 Record 转成实例或 HashMap。

| 接口 | 语义 |
| --- | --- |
| `contract_ref(name)` | 构造开放符号引用；只有递归组绑定后才能作为数据契约 |
| `contract_recursive(definitions,root)` | 将定义记录闭合为以 root 为入口的递归契约组 |
| `contract_list(element)` | 仅 List；每个元素满足子契约 |
| `contract_tuple(elements)` | 仅 Tuple；长度与描述列表相同，逐位置检查 |
| `contract_record(required, optional=null, allow_extra=false)` | 仅 Record；required／optional 是字段到子契约的记录，两组字段不能重叠 |
| `contract_union(alternatives)` | 任一子契约接受即可；按声明顺序检查 |
| `contract_optional(contract)` | 等同于与 Null 的联合 |
| `contract_map(key,value)` | 仅原生 HashMap；所有现有键和值分别满足契约 |
| `contract_accepts(contract,value)` | Bool；接受任意有效子契约表示，普通不匹配返回 false |
| `contract_info(contract)` | 接受任意有效子契约表示，返回不可执行的诊断 Record |

tuple／union 构造参数必须是 List，不消费任意迭代器；被 tuple 契约检查的值则必须是 Tuple。十个函数既是全局内建，也是 `std/contracts` 同名导出。调用、绑定和模块导入沿用现有 AST 与 Program IR；没有新增关键词、泛型注解语法或另一套执行器。

## 可省略字段与可空值

```chaos example=contracts-optional-fields
let Request = contract_record({name: "String"}, {note: contract_optional("String")});
[contract_accepts(Request, {name: "Ada"}),
 contract_accepts(Request, {name: "Ada", note: null}),
 contract_accepts(Request, {note: "missing name"}),
 contract_accepts(Request, {name: "Ada", extra: 1}),
 contract_accepts(contract_record({name: "String"}, null, true), {name: "Ada", extra: 1})]
```

结果为 `[true,true,false,false,true]`。optional 记录表示该字段可以不存在；contract_optional 表示值可以为 null。可空的必需字段仍必须存在。allow_extra 为 true 时允许未列出的字段，不为它们附加隐含的值契约。字段名是精确字符串，不按大小写、前缀或模型理解猜测。

## 固定元组、联合与通用键映射

```chaos example=contracts-tuple-union
let Pair = contract_tuple(["String", "Int"]);
let Choice = contract_union([Pair, "Null"]);
[contract_accepts(Choice, ("run", 42)), contract_accepts(Choice, ["run", 42]),
 contract_accepts(Choice, null), contract_accepts(Choice, ("run", 42, 43))]
```

结果为 `[true,false,true,false]`。空描述列表可声明空 Tuple；空 List 满足任意逐项 List 契约。联合不产生包装值，也不改变普通相等或模式匹配规则。

```chaos example=contracts-map
let Tasks = contract_map(contract_tuple(["String", "Int"]), contract_record({score: "Int"}));
let good = hash_map([(("job", 1), {score: 42})]);
let bad = hash_map([(("job", 1), {score: "42"})]);
fn score(tasks: Tasks) -> Int { tasks[("job", 1)].score }
[score(good), contract_accepts(Tasks, bad), contract_accepts(Tasks, {job: 42})]
```

结果为 `[42,false,false]`。检查 Map 时遍历已有条目，不重新计算哈希或调用用户相等；之后普通索引仍执行原有 HashMap 协议。契约不为 Map 新增哈希键种类。契约描述值按身份相等，同形描述不自动成为同一个值，也不能作为 HashMap 键。

## 递归树与互递归数据

递归属于契约代数，不需要给源码增加关键词或从类名猜类型。引用名称是非空原生 String，最长128字符；它不必是标识符，不进行大小写或 Unicode 归一化。definitions 是含1至128项的原生 Record，root 必须精确指向其中一个名字。构造时快照全部定义，不依赖此后修改原记录。

```chaos example=contracts-recursive-tree
import "std/contracts" as C;
let Tree = C.contract_recursive({
  Node: C.contract_record({value: "Int", children: C.contract_list(C.contract_ref("Node"))})
}, "Node");
fn total(node: Tree) -> Int {
  node.value + fold(fn(sum, child) { sum + total(child) }, 0, node.children)
}
[total({value: 40, children: [{value: 2, children: []}]}),
 C.contract_accepts(Tree, {value: 0, children: [{value: "bad", children: []}]})]
```

结果为 `[42,false]`。每次引用在它所属组中解析；沿 children 下探到实际 List 元素才消费一层数据。完整程序也可直接运行 [recursive-contracts.chaos](../examples/recursive-contracts.chaos)。同一机制支持任务包含步骤、步骤包含子任务的互递归：

```chaos example=contracts-mutual
let Task = contract_recursive({
  Task: contract_record({name: "String", steps: contract_list(contract_ref("Step"))}),
  Step: contract_record({task: contract_optional(contract_ref("Task"))})
}, "Task");
[contract_accepts(Task, {name: "build", steps: [{task: {name: "check", steps: []}}]}),
 contract_accepts(Task, {name: "build", steps: [{task: {name: 42, steps: []}}]})]
```

结果为 `[true,false]`。List、Tuple、Record 字段和 HashMap 键／值都可以形成消费边；联合只选择分支，引用别名只跳转定义，二者不会消费数据。每一个循环都必须包含消费边。因此 `A = ref(A)`、互相跳转的别名、含自身无消费分支的联合均拒绝，即使同组另有正确分支。所有定义都验证闭合性与循环条件，不能用不从 root 到达的定义隐藏非法引用。

```chaos example=contracts-open-template
let link: Contract = contract_ref("Node");
let template = contract_record({next: contract_optional(link)});
let Chain = contract_recursive({Node: template}, "Node");
[contract_info(link), contract_info(Chain).root,
 contract_accepts(Chain, {next: {next: null}})]
```

结果为 `[{kind:"ref",name:"Node"},"Node",true]`。开放引用与模板可以作为普通 Contract 值保存、传参、组合和诊断；数据检查和实际注解解析要求闭合。`contract_accepts` 不会因数据为空或联合先遇到 Any 就忽略开放引用；函数参数、默认值、返回、变量和类字段的实际契约不能留下待绑定引用。`descriptor: Contract` 约束描述值本身，仍可保存开放模板。

嵌套的闭合组拥有独立名字空间，不捕获外层引用。相同模板可以在多个组中重用，各组绑定互不影响。真实 Class／DataType 子契约继续使用原有身份和所有权规则，递归不会引入类名字符串查找。有限树和共享的无环子树可检查。包含递归组的数据检查会拒绝实际 List／Tuple／Record／HashMap 结构化载荷容器图中的引用循环，报告 argument_error；共享无环子树不会被当作循环。此检查不深入名义 Class 叶对应实例的任意字段，不执行对象协议，也不改变既有非递归契约的循环处理。节点／深度超限仍报告资源限制；不按“已经访问过所以通过”接受循环数据。这里提供的是有界运行时数据合同，不是无限结构证明。

本设计参考 [Racket 的递归与互递归 flat contract](https://docs.racket-lang.org/reference/data-structure-contracts.html)，但 Chaos 使用自己的不可变描述值、词法引用与预算，不承诺 Racket 语法兼容或允许任意用户谓词。

## 原有调用与赋值边界

```chaos example=contracts-rest-assignment
let Ints = contract_list("Int");
fn total(*items: Ints) -> Int { fold(fn(sum, item) { sum + item }, 0, items) }
var values: Ints = [40];
let rejected = try { values = ["bad"]; false } catch error { true };
[total(20, 22), values, rejected]
```

结果为 `[42,[40],true]`。剩余参数契约检查完整 List；具名剩余参数可以用 Record 契约检查完整记录。实参、返回值、初始绑定和后续赋值均沿用现有检查路径，失败的赋值保留旧值。冷 Task／Generator 创建时先检查实参；Task 的返回契约在实际完成时检查，Generator 的返回契约约束最终 return，不约束每次 yield 的值。检查只证明当前边界上的值满足条件，不冻结或复制其中的用户实例，也不监控以后发生的状态变更。

真实 Class 子契约按类身份与 C3 继承检查，DataType 沿用标签数据的原有纯结构合同。检查不调用 getter、`__instancecheck__`、哈希、相等、迭代器、智能体或工具。非法描述、所有权错误、预算限制和硬控制不会被 contract_accepts 伪装为 false。

## 诊断、工具链与部署

contract_info 以 kind 区分形状：builtin／class／data 为 `{kind,name}`；list 包含 element；tuple 包含 elements；record 包含 required、optional、allowExtra；union 包含 alternatives；map 包含 key 和 value。ref 为 `{kind:"ref",name}`；recursive 包含 definitions 诊断记录与 root 字符串。引用保持符号名，不展开成无限树。子项递归采用相同结构。contract_optional 返回的 kind 是 union。类名只是诊断文字，不是恢复真实类身份的协议。

每个 Tuple 描述最多10000项，Record 的两组字段合计最多10000项，联合为1至128个选项。描述、闭合性、循环验证及数据检查共用20000节点和100层预算，失败的联合分支也消耗同一预算；组合预算可能先于单个容器的10000项上限耗尽。100层计入契约节点、引用解析和联合分支的递归步骤，不保证能检查100层数据树；可成功构造的长别名链也可能在检查时触及深度限制。原步数、时间、取消和所有权限制同时生效。每次名称检查及每次描述输出另有250000字符预算；共享描述图在输出中重复展开时按实际出现次数计费。类型不匹配报告结构路径，最多保留1000字符，长字段名先截断显示，不改变实际字段匹配。构造和检查是同步原生操作，没有隐式 await。

描述值只属于创建它的 Evaluator。外部 JSON／SDK／HTTP 描述采用 `{$contract: info}`，不会恢复活描述、类句柄或授权；在宿主工程中重新执行模块声明即可获得本解释器的契约。文档 [安装与部署](INSTALLATION.md)和 [SDK](SDK.md)给出把便携包放入其他项目目录的完整入口；[可运行示例](../examples/contracts.chaos)同时适用于 CLI、IDE 与无界面 SDK。

IDE 从统一 NativeSpecs 显示十个接口的签名、Contract 返回类型与 contract_info 的诊断字段。别名注解保留源代码拼写，具体载荷及字段保守为 Any；分析器不执行用户源码求得契约。源码格式化、高亮、IR、宏和可视化共享原节点。系统 IDE 智能体的 language.reference 主题来自已安装且经过校验的文档目录，组合契约指南因此可直接检索，后续新增指南无需维护另一份关键词列表。

AgentTool 的既有 input_type／output_type 元数据接口不变；将带契约的普通语言函数作为实现时，在实际调用边界检查载荷。Team 角色同样执行这些函数；std/swarm 状态仍遵守有限纯 JSON 快照合同，递归契约可以检查这种嵌套状态，不能把活描述值放进状态。这不自动为模型供应商生成 JSON Schema，不替代工具授权，也不扩展泛型方法的优先级算法。泛型 specializers 不接受组合 Contract；内建字符串 `"Contract"` 只特化描述值本身。用户谓词、自动转换、完整静态推导及全部 Python／Lisp 类型系统仍不在当前能力范围内。
