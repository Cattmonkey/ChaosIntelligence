# 集合、候选与访问状态

Set 是原生持久集合，用于智能体候选去重、已访问状态、角色标识与工具名称集合。它与函数、宏、异步执行和 Program 自编程共享同一语言运行时，界面只是语法树的另一种编辑入口。集合表不可原地修改，按首次插入顺序迭代，普通相等按成员比较；用户对象成员仍保持原身份与自身状态。

## 创建与去重

```chaos example=set-first
let candidates: Set = {20, 22, 20};
[length(candidates), collect(candidates), fold(fn(total, x) { total + x }, 0, candidates)]
```

结果是 `[2,[20,22],42]`。`{}` 继续表示空 Record；空集合可以写 `set_of()` 或 `{*[]}`。`set_of(items=[])` 按同步迭代协议消费 List、Tuple、String、Set、Generator、Iterator 或用户迭代对象，逐项校验和去重。构造器是普通函数名，可以在局部遮蔽；原生字面量不依赖这个名称。

```chaos example=set-empty-spread
let empty = {*[]};
let values = {*(20, 22, 20), *"aa", 42};
[type_of({}), type_of(empty), collect(values)]
```

结果是 `["Record","Set",[20,22,"a",42]]`。展开仅允许集合的直接元素位置和已有的调用参数位置；不支持集合 `**record`，也不支持展开形式的推导头。每个操作数从左到右求值一次，展开源消费完毕后才开始下一个操作数。

Set 与 [HashMap](HASH_MAPS.md)共享运行时的用户哈希／相等协议；Set 表示成员关系，HashMap 表示键值关联，Record 仍是 String 键记录。没有另设 HashSet 类型。

## 原生键与纯集合

原生纯键包括 Null、Bool、Int、有限 Number、String、Fraction、Decimal、Complex，以及递归满足同样条件的 Tuple、Set。运行时还接受符合下面哈希协议的 Instance；Tuple 和嵌套 Set 可递归包含这些用户键。`true`、`1`、`1.0` 是三个不同成员；`0.0` 和 `-0.0` 是同一成员并保留先出现的值。Tuple 按顺序比较，嵌套 Set 不按插入顺序比较。

```chaos example=set-keys
let keys = {true, 1, 1.0, (20, 22), {2, 3}, {3, 2}};
[length(keys), {1, 2} == {2, 1}, {(1, 2)} == {(2, 1)}, contains(keys, true)]
```

结果是 `[5,true,false,true]`。同类型等值 Fraction 和规范化 Decimal 去重，Complex 保留实际数值分量规则。List、Record、Class、Function、Agent、Task、HashMap 等不直接成为成员，包括藏在 Tuple 中的这些不可哈希值。用户 Instance 可按协议成为成员，其普通字段仍受语言所有权与能力检查。无效查找键同样报错。

构造器仅收集到纯键时得到无 Evaluator 所有者的 Set，可以作为已验证的宿主数据跨解释器使用；宿主 `SetValue(...)` 及纯宏构造仍只允许纯键，绝不运行用户协议。运行时一旦引入用户对象，会得到创建 Evaluator 所有的协议集合；更新、删除与代数运算继续保留其所有权和收窄后的能力，即使最后只剩纯成员或空集合，也不会自动变回可跨解释器传递的纯集合。两种值的公开类型都叫 Set。

## 用户哈希与相等

```chaos example=set-user-protocol
class Key {
  var number = 0;
  fn __init__(self, number) { self.number = number; null }
  fn __hash__(self) { hash(self.number) }
  fn __eq__(self, other) {
    if isinstance(other, Key) { self.number == other.number } else { false }
  }
}
let first = Key(20);
let before = {first, Key(22), Key(20)};
let after = set_add(before, Key(42));
[map(fn(key) { key.number }, before), map(fn(key) { key.number }, after),
 length(before), contains(before, Key(20))]
```

结果是 `[[20,22],[20,22,42],2,true]`。等价重复项保留最早的真实成员与位置，不复制该实例。对象的 `__hash__` 必须同步返回严格 Int，Bool 不算 Int；`__eq__` 沿原运算协议和 `NotImplemented` 回退，最终必须同步得到 Bool。它们在普通调用、当前能力与资源预算内执行，可产生明确授权的语言效果，但不会隐式 await 冷 Task 或消费 Generator。

没有声明 `__eq__`／`__hash__` 的普通实例使用当前解释器内的身份哈希。较近类声明 `__eq__` 会禁用较远继承的哈希，除非同层或更近类明确重新声明 `__hash__`；`__hash__ = null` 明确不可哈希。这些规则及协议错误与 HashMap 共用实现，不按类名或源码文本识别。

哈希只用于寻找候选，相同摘要之后仍须检查键相等。只要成员还存在于任何集合版本中，其哈希和相等关系就必须稳定；集合表保存插入时的成员哈希，持久代数和 `hash(setValue)` 复用这些摘要，不监控字段变化或重新执行所有旧键哈希。修改对象的身份字段、类协议或依赖的外部状态会破坏这个使用合同；集合不可变并不等于对象深复制。已经发生的用户效果不会因后续失败而回滚。

```chaos example=set-protocol-algebra
class Key {
  var n = 0;
  fn __init__(self, n) { self.n = n; null }
  fn __hash__(self) { hash(self.n) }
  fn __eq__(self, other) { if isinstance(other, Key) { self.n == other.n } else { false } }
}
fn numbers(values) { map(fn(key) { key.n }, values) }
let left = {Key(3), Key(1), Key(2)};
let right = {Key(2), Key(4), Key(3)};
[numbers(left | right), numbers(left & right), numbers(left - right),
 numbers(left ^ right), {Key(1)} < left, set_is_disjoint(left, {Key(9)}),
 numbers(set_discard(left, Key(1)))]
```

结果是 `[[3,1,2,4],[3,2],[1],[1,4],true,true,[3,2]]`。普通集合相等、查找、去重、交并差、包含关系与 disjoint 都支持用户键协议。纯模式匹配则不能执行这些回调：纯集合沿用无钩子的结构比较；协议集合只按集合对象身份比较，没有新增无序解构模式。

```chaos example=set-protocol-nested-hash
class Key {
  var n = 0;
  fn __init__(self, n) { self.n = n; null }
  fn __hash__(self) { hash(self.n) }
  fn __eq__(self, other) { if isinstance(other, Key) { self.n == other.n } else { self.n == other } }
}
let objects = {Key(20), Key(22)};
let numbers = {22, 20};
let mapping = hash_map([((objects,), 42)]);
[objects == numbers, hash(objects) == hash(numbers), mapping[(numbers,)],
 hash({objects}) == hash({numbers})]
```

结果是 `[true,true,42,true]`。Set 与 Tuple 可递归作为外层集合成员或 HashMap 键。公开 `hash(Set)` 使用与插入顺序无关的成员公共哈希，纯集合和协议集合遵守同一规则；守约的用户键与原生值相等时也必须具有相同哈希。返回的 Int 不是跨版本、跨进程的持久标识，算法可以演进；保存和交换程序应使用源码及明确数据格式，不能把哈希数字当作永久内容地址。

## 函数式更新与运算

```chaos example=set-algebra
let before = {1, 2}; let other = {2, 3};
let after = set_add(before, 42);
[collect(before | other), collect(before & other), collect(before - other),
 collect(before ^ other), before < after, set_is_disjoint(before, {3}),
 collect(set_discard(after, 2)), collect(before)]
```

结果是 `[[1,2,3],[2],[1],[1,3],true,true,[1,42],[1,2]]`。`|` 并集保留左侧顺序再追加右侧新成员；`&` 交集和 `-` 差集保留左侧顺序；`^` 对称差先左侧独有成员，再右侧独有成员。`<`／`<=` 是真子集／子集，`>`／`>=` 是真超集／超集，不进行字典序排序。上述原生集合运算要求双方是 Set；外部 Instance 仍可通过既有运算协议处理混合操作数。

`set_add(value, item)`、`set_discard(value, item)` 返回集合值，不修改已有值；`set_is_disjoint(left, right)` 返回 Bool。已有的 `set(record, key, value)` 仍是 Record 更新函数。`length`、`bool`、`contains` 和同步迭代支持 Set；`if`／`while` 条件仍严格要求 Bool。List 专用的 append、sort 等合同不扩大，先 `collect` 可显式得到 List。

## 推导、作用域与异步

```chaos example=set-comprehension
let offset = 20;
let values = {offset + x for (x, enabled) in [(22, true), (22, true), (99, false)] if enabled};
collect(values)
```

结果是 `[42]`。推导是立即执行的；每个来源只在进入相应子句时求值，过滤条件必须是 Bool，结构目标使用统一模式匹配器，变量不泄漏到外部。

```chaos example=set-async
async fn* candidates() { yield 21; await sleep(0); yield 21 }
async fn score(value) { await sleep(0); value * 2 }
let distinct = {await score(value) async for value in candidates()};
collect(distinct)
```

结果是 `[42]`。异步来源与 await 只在已有的顶层／异步函数作用域允许；Set 本身仍是同步可迭代值。普通构造器与展开不会隐式 await。头表达式在去重前执行，因此重复值也会产生其显式效果。

每个字面量、构造或推导最多处理 10,000 次相应输入／产出尝试，重复成员也计数；每个推导来源仍有既有消费上限。标量元素与所有展开源共享字面量额度。验证在每项求值后立即进行，非法元素会阻止后续求值和游标消费；已经发生的效果不会回滚。同步消费借用游标，不因成功或失败自动关闭，资源释放应由作用域管理。精确上限沿用迭代消费器的结束探测规则。

纯键操作继续限制遍历节点、深度、字符串长度和整数位数，协议键沿共享哈希预算和普通执行步数／时间限制；哈希冲突比较同样计费。纯共享 Tuple 结构可缓存以避免指数展开，含用户回调的键不能按纯值缓存来省略原有调用效果。硬取消或资源超限不发布半成品集合，活构造器和挂起表达式的已求值成员也参与统一捕获图。

## 智能体与数据边界

```chaos example=set-channel
let messages = channel(1);
await channel_send(messages, {"planner", "worker", "worker"});
let roles = (await channel_receive(messages)).value;
channel_close(messages);
collect(roles - {"planner"})
```

结果是 `["worker"]`。Channel 可在同一运行环境传递合法 Set，Set 成员递归纳入消息检查与大小预算；Instance 成员在此验证自身所有权，其字段访问、调用与捕获仍沿既有 owner／epoch／能力检查。Team 的语言函数可直接接收 Set，AgentTool 和群体工作函数也可在内部使用它；模型工具、Session、Process 等严格 JSON 入口仍拒绝原生集合。`std/swarm` 的快照、prepare 结果和 worker 返回值继续要求有限 JSON；工作函数可以内部使用协议集合或调用 Team，再将结果显式投影为状态数据。`collect` 只将容器转成 List，若元素是用户实例，还需显式读取允许的数据字段，才能满足 JSON 数据合同。

协议集合不能跨 Evaluator；Instance 字段、回调、接续和其他活值在字段访问、实际调用或捕获时沿既有 owner、epoch 与当前能力检查，不能借集合绕过这些边界；这不意味着发送消息时会提前深扫任意实例字段。迭代与 collect 保留成员身份，不为放宽权限而克隆键；低权限上下文若无法安全暴露原键，会被拒绝。字段、模块、函数返回、Channel、HashMap、泛型值特化等嵌套路径均沿同一捕获与投影规则；保存 next 的旧别名不能恢复已收窄的权限。

```chaos example=set-json
let value = {20, 22};
let description = parse_json(json_stringify(value));
[type_of(value), type_of(description), description, collect(value)]
```

结果是 `["Set","Record",{"$set":[20,22]},[20,22]]`。旧 `$set` 结构保持不变；协议集合的合法成员也只能投影为安全诊断描述，例如实例仅呈现类名，而不是活对象及其方法。运行结果、json_stringify 和调试展示不执行用户哈希／相等／getter，仍检查所有权及预算；parse_json 不恢复原生 Set、实例或行为。调试器另以 `type: "Set"` 与有界 `items` 展示成员。

## 宏、编辑器与自编程

```chaos example=set-macro
macro values() { syntax_literal({x for x in (20, 22, 20)}) }
macro empty() { syntax_literal(set_of()) }
let set_of = fn() { "shadowed" };
[collect(values!()), type_of(empty!())]
```

结果是 `[[20,22],"Set"]`。纯宏支持集合字面量、纯集合运算、上述四个集合函数和同步纯数据推导；展开与 set_of 只接收编译期 List、Tuple、Set、String，不运行用户迭代器。`syntax_literal` 对空集合生成 `{*[]}` 原生语法，不插入可被遮蔽的函数调用。quote／unquote 中的推导变量遵守卫生规则。

`set(items)` 与 `set_comp(element, clauses)` 是 `chaos.program/1` 的增量节点，原有节点含义不变。set 的 items 不能为空，需要空集合时使用包含 `spread(list([]))` 的节点。CLI、SDK、HTTP、Program 修改、图形编辑与格式化读写相同结构；编辑器提供 Set 高亮、契约提示、集合函数参数帮助以及推导变量导航／重命名。调试器展示有界 Set 成员，不执行用户代码。

可直接运行[用户对象集合示例](../examples/set-protocol.chaos)。本版本没有提供可变集合、无序集合模式或完整 Python/Lisp 兼容。剩余边界见[语言路线与完整性](LANGUAGE_ROADMAP.md)。

HashMap 的递归哈希、实例哈希、碰撞查询、持久更新、构造与相等，以及 Set 的构造、提升、成员判断、集合代数、更新和字面量求值，使用16种真实[容器执行帧](EXECUTION_FRAMES.md#持久哈希容器的执行状态)。帧保存实际草稿、游标、循环位置、共享预算、待决协议与权限恢复状态；原有调用次序、检查点、首个等价键的身份、插入时哈希、借用与取消合同保持不变。纯值计算仍是同步叶操作。这没有增加保存／复用 continuation、独立 GC 或通用 AOT 的能力。
