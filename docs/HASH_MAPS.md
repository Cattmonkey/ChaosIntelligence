# 通用键映射与用户哈希

HashMap 是原生有序、持久映射，用来把结构化任务标识、数值或用户对象关联到语言值。它支持用户 `__hash__` 和 `__eq__`，沿原 AST、效果系统、能力边界和调度器执行；页面不按键的文本形式猜测类型。Record 保留 String 键；Set 与 HashMap 共享用户键协议，分别表达成员关系和键值关联。

## 值式构造与更新

```chaos example=hash-map-persistent
import "std/hash" as H;
let empty: HashMap = H.hash_map();
let before = H.hash_set(empty, (20, 22), "old");
let after = H.hash_set(before, (20, 22), "new");
[before[(20, 22)], after[(20, 22)], H.hash_get(after, "missing", "fallback"),
 length(empty), H.hash_info(after).size]
```

结果为 `["old","new","fallback",0,1]`。Tuple 可以作为一个完整的复合键；`mapping[key]` 是普通条目读取。更新返回新映射，旧值保持原关联，嵌套可变对象仍共享实际身份。“持久”指保留旧版本的数据结构，不表示把活对象和调用栈自动保存到磁盘。

| 接口 | 结果与边界 |
| --- | --- |
| `hash(value)` | Int；原生键哈希，或执行用户同步 `__hash__` |
| `hash_map(entries=[])` | HashMap；同步消费条目来源，每项必须是长度2的 List 或 Tuple |
| `hash_get(mapping,key,default=null)` | 找到时返回值，未找到返回 default |
| `hash_set(mapping,key,value)` | 新 HashMap；新增或替换关联 |
| `hash_remove(mapping,key)` | 新 HashMap；不存在时内容不变 |
| `hash_has(mapping,key)` | Bool；按哈希和键相等检查存在性 |
| `hash_items(mapping)` | 按键顺序返回二元 Tuple 的 List |
| `hash_keys(mapping)`／`hash_values(mapping)` | 按键顺序返回原键／经过能力投影的值的 List |
| `hash_info(mapping)` | `{size:Int,ordered:Bool,persistent:Bool,maxEntries:Int,keyPolicy:String}` |

上述十个函数同时为全局内建和 `std/hash` 的同名导出。hash_info 不调用用户键协议；ordered／persistent 为 true，maxEntries 为10000，keyPolicy 为 `"native-hash-equality"`。哈希是内部索引摘要，不是密码学摘要、内容标识或跨进程稳定存储格式。

## 顺序与相等

重复插入等价键会替换值，保留第一次的键对象及位置。删除再插入则移动到末尾。普通 `==` 比较映射的键和值，忽略插入顺序，可调用用户相等协议。纯模式匹配不执行这些回调，对 HashMap 使用身份比较；它没有新增通用键记录解构语法。

```chaos example=hash-map-order
let mapping = hash_map([("a", 1), ("b", 2), ("a", 3)]);
let reversed = hash_map([("b", 2), ("a", 3)]);
let moved = hash_set(hash_remove(mapping, "a"), "a", 9);
[hash_keys(mapping), hash_values(mapping), type_of(hash_items(mapping)[0]),
 mapping == reversed, hash_keys(moved)]
```

结果为 `[["a","b"],[3,2],"Tuple",true,["b","a"]]`。`mapping[key]` 缺键报 `key_error`；hash_get 的 default 在调用前按普通实参求值规则计算，它不是延迟回调。

HashMap 支持 length、contains、显式 bool 和同步迭代；迭代产出键。`if`／`while` 仍要求严格 Bool。方括号写入和删除不成为原地修改接口，使用 hash_set／hash_remove；Record 的 set／remove、List 的 append 等已有合同保持原样。

## 原生键与用户键

允许 Null、Bool、Int、有限 Number、String、Fraction、Decimal、Complex、递归 Set／Tuple，以及符合哈希协议的 Instance。List、Record、Class、DataType、Function、HashMap 和其他活句柄不能作为键；它们可以是符合语言所有权与能力规则的值。HashMap 本身不可哈希。Set 可递归包含守约的用户键，并与纯集合使用统一的无序公共哈希；其插入时摘要会被后续持久操作复用。hash 的数值不作为跨版本持久格式。

```chaos example=hash-map-strict-keys
let mapping = hash_map([(true, "bool"), (1, "int"), (1.0, "number")]);
[length(mapping), mapping[true], mapping[1], mapping[1.0]]
```

结果为 `[3,"bool","int","number"]`，保持语言原有的严格类型相等。等值 Decimal 的不同小数位表示具有相同哈希；Number 的正零和负零为同一键。用户定义相等的键必须返回相同哈希；哈希碰撞后仍比较键，不能只凭摘要相同认定相等。

```chaos example=hash-map-user-key
class JobKey {
  var number = 0;
  fn __init__(self, number) { self.number = number; null }
  fn __hash__(self) { self.number }
  fn __eq__(self, other) {
    if isinstance(other, JobKey) { self.number == other.number } else { false }
  }
}
let key = JobKey(7);
let before = hash_map([(key, 40)]);
let after = hash_set(before, JobKey(7), 42);
[before[JobKey(7)], after[JobKey(7)], length(after)]
```

结果为 `[40,42,1]`。`__hash__` 必须同步返回严格 Int，Bool 不被当作 Int；用户相等沿用原运算协议及 `NotImplemented` 回退，最终必须同步得到 Bool。方法可产生当前调用允许的普通语言效果，但冷 Task／Generator 不会被隐式等待或消费。方法失败按原错误、取消和资源协议传播，已发生的外部效果不回滚。

没有声明 `__hash__` 或 `__eq__` 的普通实例使用解释器内的身份哈希。较近类声明 `__eq__` 会禁用较远基类继承的哈希，除非同层或更近类显式重新声明 `__hash__`；`__hash__ = null` 明确不可哈希。不支持的键和不可哈希实例报 `unhashable_type`，哈希协议的错误返回值或异步形式报 `argument_error`。协议按真实类成员解析，不通过名字文本猜测实现。

键在仍存在于任何映射版本时，哈希和相等关系必须稳定。运行时保存插入时的哈希，不监控字段变更，也不会隐式重建所有历史映射。相同键引用在哈希匹配后可以直接命中，即使用户相等不自反。不要让构成键语义的实例字段、类协议或外部可变状态在存放期间变化。

## 同步数据源与泛型函数

```chaos example=hash-map-function
import "std/generic" as G;
fn* entries() { yield ("plan", 20); yield ("execute", 22) }
let state = hash_map(entries());
let total = G.generic_method(G.generic_function("total"), ["HashMap"], fn(mapping) {
  fold(fn(sum, key) { sum + mapping[key] }, 0, mapping)
});
[total(state), collect(state), contains(state, "plan"), bool(state)]
```

结果为 `[42,["plan","execute"],true,true]`。构造器只消费外层同步迭代协议；单个条目不是任意可迭代对象，必须直接为二元 List／Tuple。异步来源需显式消费并整理为条目；构造不会偷偷 await。游标借用、结束探测、关闭和错误规则沿用[函数组合](FUNCTION_COMPOSITION.md)。

每张映射最多10000个条目，构造最多消费10000次输入尝试，重复键也计数。键哈希遍历最多20000个节点、100层；原步数、时间、调用深度和值预算同时生效。持久更新不承诺原地 O(1) 修改，哈希和相等可能执行用户代码，复杂度与这些协议共同决定。

## 智能体、权限与外部接口

映射可保存真实语言函数、智能体组件和状态，成为普通 AgentTool／Team 实现的内部数据结构。取值、提取键和值以及迭代保持当前调用和映射的能力上限，不因传入更高权限的函数而获得额外能力。实例键保留身份，不为收窄权限而悄悄克隆；返回的函数值可能因权限投影而具有不同对象身份；低权限上下文若不能安全暴露原键，操作可能被拒绝。

HashMap 属于创建它的 Evaluator，不可跨解释器直接复用。容器本身不另造轮次期限，但其中的 Task／Generator 等值仍遵守原执行轮次与所有权规则。JSON、SDK／HTTP 输出和调试描述只给出 `{"$hashMap":{"size":N,"ordered":true,"persistent":true}}` 摘要，不包含实际条目，不恢复可执行 Map 或用户回调；需要数据交换时显式调用 hash_items／hash_keys／hash_values，再按宿主入口要求投影为纯数据。模型或网络返回的普通 Record 不会自动变成 HashMap。

[完整示例](../examples/hash-maps.chaos)可通过 SDK、原 CLI 或 IDE 运行。源码、现有 call／subscript／for AST、IR、卫生宏、格式化与可视化共用实现。IDE 从 NativeSpecs 提供签名、返回类型和 hash_info 字段，键和值保守为 Any，不执行键协议推断结果。Set 的用户对象、嵌套哈希与纯模式边界见[集合规范](SETS.md)。本能力不等于 Python dict 的全部接口、完整 CLOS 或全部 Python／Lisp 标准库。

HashMap 的递归哈希、实例哈希、碰撞查询、持久更新、构造与相等，以及 Set 的构造、提升、成员判断、集合代数、更新和字面量求值，使用16种真实[容器执行帧](EXECUTION_FRAMES.md#持久哈希容器的执行状态)。帧保存实际草稿、游标、循环位置、共享预算、待决协议与权限恢复状态；原有调用次序、检查点、首个等价键的身份、插入时哈希、借用与取消合同保持不变。纯值计算仍是同步叶操作。这没有增加保存／复用 continuation、独立 GC 或通用 AOT 的能力。
