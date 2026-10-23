# 增强赋值、幂与位运算

Chaos 0.50 将增强赋值作为独立的 `aug_assign` 语法节点。变量、对象属性和用户条目协议共用明确的读取、运算、写入顺序；协议方法仍是语言函数，可以调用智能体、使用工具、挂起和接受取消。网页编辑器、命令行、SDK、LSP 与 Program 自我编程操作使用同一棵语法树。

## 数值表达式与赋值

```chaos example=augmented-numeric
var mask: Int = 5;
mask <<= 2;
mask |= 2;
[mask, mask & 7, ~mask, 2 ** 3 ** 2, -2 ** 2]
```

结果为 `[22,6,-23,512,-4]`。`**` 右结合；`-2 ** 2` 是 `-(2 ** 2)`，`2 ** -2` 是 0.25。完整优先级见[语言参考](LANGUAGE.md)。`await f() ** 2` 先等待 f 的返回任务，再求幂；不会自动等待运算协议返回的 Task。

| 增强赋值 | 优先尝试的实例协议 | 普通回退协议 |
| --- | --- | --- |
| `+=` | `__iadd__` | `__add__` / `__radd__` |
| `-=` | `__isub__` | `__sub__` / `__rsub__` |
| `*=` | `__imul__` | `__mul__` / `__rmul__` |
| `/=` | `__itruediv__` | `__truediv__` / `__rtruediv__` |
| `%=` | `__imod__` | `__mod__` / `__rmod__` |
| `**=` | `__ipow__` | `__pow__` / `__rpow__` |
| `&=` | `__iand__` | `__and__` / `__rand__` |
| `\|=` | `__ior__` | `__or__` / `__ror__` |
| `^=` | `__ixor__` | `__xor__` / `__rxor__` |
| `<<=` | `__ilshift__` | `__lshift__` / `__rlshift__` |
| `>>=` | `__irshift__` | `__rshift__` / `__rrshift__` |

二元 `& | ^ << >>` 使用表中普通协议，一元 `+`、`~` 分别使用 `__pos__`、`__invert__`。原始位操作仅接受 Int，排除 Bool 和浮点数。负位移计数报语言错误。左移在分配前检查 14,000 bit 整数上限；零的大幅左移仍为零。超过有效位数的右移返回 0 或 -1，不转换为宿主机器字长。幂沿用 `pow` 的有限结果与预算检查。

`//` 和 `//=` 仍开始行注释，整除请调用 `floordiv`。`@` 仍用于装饰器，没有矩阵乘法。增强赋值是语句，不能作为任意子表达式；可以通过 `do { ... }` 获取语句结果。不支持连锁赋值。增强赋值不接受结构解构目标；普通结构赋值见[解构绑定](PATTERN_BINDINGS.md)。

## 目标仅求值一次

对于 `receiver()[key()] += rhs()`，运行时依次执行：

1. 求 receiver 和 key 各一次，读取原值一次；名字目标则捕获最近的可写 Cell。
2. 求右侧值一次。
3. 先尝试原值的原位协议；只有缺失或返回真正的 NotImplemented 才进入[普通双向分派](OPERATORS.md)。方法报错直接传播。
4. 将运算结果写回捕获的 Cell、接收对象和键。属性描述符在写入时重新查找，右侧可以改变其实现。

```chaos example=augmented-target
var trace = [];
class Box {
  var value: Int = 40;
  fn __getitem__(self, key) {
    trace = append(trace, "read"); self.value
  }
  fn __setitem__(self, key, value) {
    trace = append(trace, "write"); self.value = value; null
  }
}
let box = Box();
fn receiver() { trace = append(trace, "receiver"); box }
fn key() { trace = append(trace, "key"); "value" }
fn rhs() { trace = append(trace, "rhs"); 2 }
let result = do { receiver()[key()] += rhs() };
[result, box.value, trace]
```

结果为 `[42,42,["receiver","key","read","rhs","write"]]`。条目 setter 仍须返回 null，增强赋值本身返回运算结果。切片条目也遵守相同规则。语言版本 1 的旧方括号属性访问与版本 2 的条目访问继续区分，见[索引兼容规则](INDEXING.md)。

不存在或不可写的名字在执行右侧和原位协议前被拒绝。类型契约在写回时检查；如 `var count: Int=1; count/=2` 会失败并保留旧 Cell 值，但之前已经产生的工具或对象副作用不会回滚。对象的成员可写性遵守既有 setter 规则，不提前锁定描述符。

## 值式容器与有状态对象

```chaos example=augmented-alias
class Counter {
  var value: Int = 1;
  fn __iadd__(self, other: Int) {
    self.value += other;
    self
  }
}
var counter = Counter();
let alias = counter;
counter += 2;
var items = [1];
let oldItems = items;
items += [2];
[counter.value, alias.value, items, oldItems]
```

结果为 `[3,3,[1,2],[1]]`。原位协议可返回自身，也可返回其他值；实际返回值决定写回内容。原生 List 和 String 的 `+=` 产生新连接值，不修改旧容器。List、Record、String 的成员或条目仍不能原位写入。`let` 绑定不能因对象可变而使用增强赋值：需要 `var counter` 才能写回。

## 智能体、异步与自我编程

协议通过同一个语言调用入口执行，允许普通函数、描述符、Agent 调用和 AgentTool 处理函数组合。效果仍受项目能力、角色策略、所有权、预算和录制规则约束。传过 JSON 的 `{"$notImplemented":true}` 只是记录，不会变成内部哨兵。

`async fn __iadd__` 返回冷 Task。未注解的 `var` 可以保存该 Task，随后显式 `await`；带类型约束的目标可能在写回时拒绝它。不会隐式启动任务，也不会根据任务最终返回的 NotImplemented 再次回退。同步协议内部等待工具效果时，调度器可以运行其他任务。

增强赋值不是跨挂起点的原子事务。例如右侧等待期间其他 Task 修改同一 Cell，最终写回仍基于此前读取的旧值。应通过 Channel、单一状态所有者或显式协调控制竞争。硬取消和资源耗尽直接传播，不触发回退或后续 setter；词法取消保护的范围见[取消语义](CANCELLATION.md)。

`program_ir`、`program_edit`、`program_probe` 和 `program_adopt` 能检查、修改、测试并采用含增强赋值的程序，包含智能体工具函数。修改 `operator` 字段使用 `+` 等不含等号的值，`target` 是 name、index 或 subscript 节点，`value` 是右侧表达式。IR schema 仍为 `chaos.program/1`；采用新程序不会改写旧运行快照。详见[自我编程](SELF_PROGRAMMING.md)。

## 宏与开发环境

```chaos example=augmented-macro
macro increment(target) {
  quote { unquote(target) += 1 }
}
var count: Int = 41;
increment!(count);
count
```

结果为 42。模板内声明的局部名字保持卫生性；修改调用者目标必须显式 unquote。展开为字面量、调用或其他不可赋值节点会在任何模块效果执行前报错。宏纯计算阶段可计算原始幂和位表达式，仍禁止可变状态和运行期对象协议；quote 内的赋值则在展开后的运行期执行。

IDE 与页面代码块使用共享的分析与渲染组件，运算符着色、名字提示和自动缩进来自解析器。结构面板可选择增强赋值和协议模板；源码格式化保留注释、结合顺序和版本语义。只读代码提供排版／原文切换，复制保留原源码；提示不执行 Agent 或用户方法。外部编辑器通过 LSP 使用相同分析、格式化和重命名。推断不确定时显示 Any，最终类型检查由运行时执行。

此设计参考 [Python 增强赋值求值顺序](https://docs.python.org/3/reference/simple_stmts.html#augmented-assignment-statements)和[数值协议](https://docs.python.org/3/reference/datamodel.html#emulating-numeric-types)，保留 Chaos 自身的值式容器、严格类型与智能体效果边界。
