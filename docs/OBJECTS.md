# 类、实例、继承与智能体组件

类把有身份的状态和方法组织在一起。`class` 是运行期声明，可以放在模块或函数的局部作用域；调用 Class 创建 Instance。它们由当前 Evaluator 持有，不是自动持久化的数据库记录。普通数据传输仍使用 List、Record 和已有的 data 类型。

## 声明、构造与绑定方法

```chaos example=objects-counter
class Counter {
  var value: Int = 0;
  fn __init__(self, start: Int) { self.value = start; null }
  fn add(self, step: Int = 1) {
    self.value = self.value + step;
    self.value
  }
}
let counter = Counter(start=40);
let advance = counter.add;
[advance(step=2), counter.value]
```

结果为 `[42,42]`。类体只接受 `let`、`var` 和命名方法声明，成员不能重名。方法显式声明首个位置参数接收实例，通常命名为 `self`；其它名字也可以。读取 `counter.add` 得到绑定方法，保存或传递它之后仍使用原来的 counter。读取 `Counter.add` 得到未绑定函数，需要自己传入接收者。

0.64 增加同步 `__new__` 分配与动态建类，完整阶段见[类创建协议](CLASS_CREATION.md)。没有自定义分配器时，构造器查找包括继承的 `__init__`。它必须是普通同步 `fn` 方法，完成值必须为 `null`；没有构造器的类只接受零参数。异步初始化写成独立的 `async fn` 工厂。尚未完成成员定义的 Class 不能构造实例，声明成功后才向外层作用域发布类名。

只有命名方法声明自动绑定接收者。`let action = fn(x) { x }` 或 Agent 字段仍保持原来的调用参数，不自动插入 `self`。方法的函数体捕获声明处外层环境和自身类名；通过 `self.value` 访问字段，裸 `value` 不会自动解析为字段。方法参数默认表达式在声明时求值，可以读取之前声明的类成员。

## 字段、默认值与修改

`object.field = value` 和显式 `object.["field"] = value` 是成员赋值，成员键必须为 String。当前语言版本中 `object[key] = value` 优先调用用户条目协议；旧版本保留成员访问行为，详见[索引兼容规则](INDEXING.md)。目标、键和值按源顺序各求值一次。`let` 字段和方法不可重新赋值，`var` 字段可以修改并检查声明的类型契约。未声明的新属性是可写的 Any 属性。List 和 Record 的元素赋值仍不支持；增强赋值的读取和写回规则见[增强赋值](AUGMENTED_ASSIGNMENT.md)。

默认值在**执行类声明时求值一次**，按成员源顺序执行。它们不会在每次创建实例时复制。实例赋值会建立自己的覆盖值；继承的 `var` 契约继续适用，子类或实例的覆盖不修改基类默认值。

```chaos example=objects-shared-defaults
class Box { var value: Int = 0 }
class Shared { var box = Box() }
class Separate {
  var box = Box();
  fn __init__(self) { self.box = Box(); null }
}
let a = Shared(); let b = Shared();
a.box.value = 42;
let c = Separate(); let d = Separate();
c.box.value = 7;
[b.box.value, d.box.value]
```

结果为 `[42,0]`。Shared 的两个实例共享默认 Box；Separate 的构造器显式建立独立 Box。`let` 限制的是成员重新赋值，不会把成员引用的可变对象递归冻结。

类成员默认表达式不能用 `return`、`break`、`continue` 或 `yield` 跳转到外围函数或循环。顶层或 async 函数里的类声明可以在默认表达式使用 await；普通 fn 中不允许借类声明绕过 await 限制。默认表达式中新定义的函数或循环仍有自己的控制流作用域。

## 继承与显式 super

```chaos example=objects-inheritance
class A { fn labels(self) { ["A"] } }
class B(A) { fn labels(self) { ["B"] + super(B, self).labels() } }
class C(A) { fn labels(self) { ["C"] + super(C, self).labels() } }
class D(B, C) { fn labels(self) { ["D"] + super(D, self).labels() } }
[D().labels(), issubclass(D, A), isinstance(D(), B)]
```

结果为 `[["D","B","C","A"],true,true]`。多重继承采用 C3 方法解析顺序，保留基类声明顺序与一致的祖先顺序。重复基类、非 Class 基类或无法建立一致顺序的层次会在执行成员默认值之前失败。

`super(klass, instance)` 沿实例实际的解析顺序，从 klass **之后**继续查找成员；它不是固定跳到第一个父类。不支持隐式零参数 super。声明方法时使用本类名字，使同一个方法在进一步派生的实例上也能协作调用。

0.68 的公开 isinstance／issubclass 可由实际元类同步定制，严格返回 Bool；[类型协议](TYPE_PROTOCOLS.md)说明它与下面的内部名义身份边界。

类身份是名义身份：两次执行同一个局部 class 声明会得到不同 Class，即使名字和字段完全相同。`value: SomeClass` 接受该类及其子类的实例。内置 `Class` 与 `Instance` 分别检查类值和实例值；`type_of` 返回这些类别名称，不返回用户类名。`class_of(instance)` 返回实际 Class，0.66 起 `class_of(Class)` 返回其原生元类；单参数 Type 的查询范围同样限 Class／Instance。Type 契约表示默认 Type 的子类，Class 契约保持原义；实际元类契约接受其创建的 Class。标量内置值尚未全部统一为 Type 实例。继承通知和类头部配置见[类创建协议](CLASS_CREATION.md)，元类和 prepare 见[原生元类](METACLASSES.md)。

## 可调用对象与四种方法

```chaos example=objects-callables
class Scale {
  let factor = 2;
  fn __call__(self, value) { self.factor * value }
}
let double: Function = Scale();
[apply(double, [21]), partial(double, value=21)(),
 compose(fn(x) { x + 2 }, double)(20)]
```

结果为 `[42,42,42]`。Class、绑定方法和具有可调用 `__call__` 的实例都满足 Function 契约。`apply`、`partial`、`compose`、参数检查和尾调用沿用统一调用入口。特殊构造／调用方法按类的继承链查找；给单个实例动态添加或覆盖 `__call__` 不改变 `instance(...)` 的含义。

```chaos example=objects-method-flavors
class Source {
  let base = 40;
  async fn value(self, extra=2) { self.base + extra }
  fn* values(self) { yield self.base; yield self.base + 1 }
  async fn* stream(self) { yield await self.value() }
}
let source = Source();
[await source.value(), collect(source.values()), await acollect(source.stream())]
```

结果为 `[42,[40,41],[42]]`。方法支持 `fn`、`async fn`、`fn*` 和 `async fn*`，返回值分别遵循普通函数、Task、Generator 和 AsyncGenerator 规则。异步方法仍在同一解释器线程推进对象状态；效果处理器可以并发，不能据此假定共享字段操作互不交错。任务预算、取消与流关闭规则见[效果规范](EFFECTS.md)。

## 模块与类型契约

Class 可以正常导出。以下入口的测试附件 `types.chaos` 内容是 `class Answer { var value:Int = 42 }; export {Answer}`：

```chaos example=objects-module
import "types.chaos" as Types;
fn checked(value: Types.Answer) -> Types.Answer { value }
let answer = Types.Answer();
checked(answer).value
```

结果为 `42`。模块记录中的 `Types.Answer`、构造实例使用的 Class 和限定类型契约引用同一身份。宏导出仍只属于编译期命名空间，不成为模块记录里的运行期值。跨不同 Evaluator 的活动类、实例和绑定方法不能直接混用。

## 宏生成对象

```chaos example=objects-macro
macro makeBox(seed) {
  quote {
    class Box {
      var value: Int = unquote(seed);
      fn read(self, bonus: Int = 2) { self.value + bonus }
    }
    Box
  }
}
let self = 40;
let Box = makeBox!(self);
[Box().read(bonus=2), self]
```

结果为 `[42,40]`。模板中的类词法名、方法接收者和局部绑定会卫生重命名；公开字段名、方法名及具名参数标签保持稳定。`unquote(seed)` 仍指调用者的 self，不会被方法参数捕获。

quote 可以生成 class 和成员赋值；宏纯求值阶段不能执行它们。封闭模板不隐式捕获外部基类、Agent、工具或类型名：基类可通过 `unquote(base)` 显式注入；类型契约可使用模板内声明的类或显式绑定的类型别名，例如 `let T = unquote(type_arg)` 后使用 `value:T`。原始源码／IR 保留宏声明和调用，展开 AST 用于运行；详见[宏规范](MACROS.md)。

## 成员查询与边界

以下是支持具名调用的实际参数名：

| 内建签名 | 行为 |
| --- | --- |
| `isinstance(value, klass)` | 调用实际元类 __instancecheck__，严格 Bool；默认 Type 按真实 MRO 判断 |
| `issubclass(child, base)` | 调用 base 实际元类 __subclasscheck__，严格 Bool；默认 Type 按真实继承 MRO 判断 |
| `class_of(value)` | 取得 Instance 的实际 Class，或 Class 的元类 |
| `super(klass, instance)` | 创建沿实例继承顺序继续查找的代理 |
| `getattr(value, name)` | 读取对象成员；缺失时抛 attribute_error |
| `setattr(value, name, item)` | 使用同样的可写性与契约检查赋值，返回 item |
| `hasattr(value, name)` | 成员缺失时返回 false；其它错误仍传播 |
| `dir(value)` | 显式执行 __dir__ 或默认目录，排序并保留用户返回的重复名称 |
| `default_getattr(value, name)` | 跳过属性钩子，沿原描述符与存储读取 |
| `default_setattr(value, name, item)` | 跳过写入钩子，沿原描述符／契约写入并返回 item |
| `default_delattr(value, name)` | 跳过删除钩子，沿原默认删除逻辑 |
| `default_dir(value)` | 无属性／目录钩子的原有成员目录 |
| `vars(value)` | 返回 Class 自身成员或 Instance 自身覆盖／新增字段的新 Record |

Instance 和实际元类支持 __getattribute__／__getattr__／__setattr__／__delattr__／__dir__；原描述符顺序可用 default_* 显式委托。普通访问有授权效果，隐式特殊方法、纯模式与显示不执行这五个钩子，见[属性协议](ATTRIBUTE_PROTOCOL.md)。

`vars` 显式提取对象的自有状态。实例投影不包含尚未覆盖的类默认值或继承字段；需要这些值时使用 `getattr` 或自己构造 Record。Class 投影包含自身声明／覆盖的成员；普通方法是未绑定 Function，描述符保留自身值，均不会执行；Super 代理不能传入 vars。

```chaos example=objects-state-projection
class Base { let inherited = 1 }
class State(Base) {
  var value = 0;
  fn read(self) { self.value }
}
let instance = State();
instance.amount = 42;
let projected = vars(instance);
let changed = set(projected, "amount", 99);
[projected, changed, instance.amount, keys(vars(State))]
```

结果为 `[{amount:42},{amount:99},42,["value","read"]]`。修改返回记录的副本不会反向修改对象的字段字典。投影保留内部值的身份：若字段本身引用另一个实例，投影里的值仍引用它；这不是深克隆，也不是自动持久化。交给 Agent 或保存时，应选择需要的普通数据字段；对象值跨宿主边界仍使用下述描述规则。

执行受统一步数、时间和集合预算约束，也限制类、实例、成员和继承规模。对象不会转发到任意 Python 属性，提供文档规定的用户运算协议及[原生元类](METACLASSES.md)，尚无析构函数及全部元对象协议；这里不是完整 Python 对象模型。

类与实例可在同一 Session 的后续提交中继续使用；Task 和生成器仍属于各自的执行周期。向宿主工具／智能体传递对象或序列化运行结果时，新对象值只产生有界描述，例如 `{"$instance":"Counter"}`，不会传递活实例、方法闭包或完整字段图。描述不能恢复对象。持久业务状态应显式保存为普通数据，再重建实例。

兼容边界：Framework／Registry、API 和异步效果工作线程使用 JSON 数据边界。低层 Evaluator 直接调用同步 Python 回调时，为兼容旧 ABI，仍可向回调传递原有的 Function、Native 或 Agent 活值；受信任回调因此可能通过旧 Function 的环境间接取得实例。这不是隔离沙箱。新对象直接作为 payload 值或其 List／Record 成员时仍转换为上述描述；高层项目能力检查不会因此改变。

Agent 字段、异步方法与可调用组件的完整运行链见[智能体语言指南](AGENT_LANGUAGE.md)，Python 注册与项目运行见[SDK](SDK.md)。

## 装饰后的成员

命名方法可使用装饰器。最终结果为普通 Function 时仍按方法绑定；其它替换值遵循自身调用规则。staticmethod/classmethod/property 是显式内置描述符，也可直接存入 let/var 成员。普通字段中的 Function/Agent 仍不自动插入 self。property 的受控 getter/setter/deleter 更新是同名成员声明的特例，其余重复成员仍拒绝。完整语义见[装饰器规范](DECORATORS.md)。

## 用户描述符和成员删除

类成员中的用户实例可以定义属性协议；同一个实例直接存于普通实例字段时仍作为原值返回，不自动触发协议。数据描述符的读写优先级与非数据描述符的实例遮蔽规则见[用户描述符规范](DESCRIPTOR_PROTOCOL.md)。普通只读字段规则保持不变，只有 getter 的用户描述符明确允许实例缓存遮蔽。

`delattr` 删除自身可写状态，删除实例覆写后可重新读到类默认值；不能从子类删除基类成员。数据描述符与 property 则由删除器处理，Class 的成员写入和删除不触发实例描述符。super 保持只读查找代理。

所有成员填充完成后，owner Class 已可构造和读取，再按固定自身成员快照执行命名通知。外层绑定仍在成功后发布。失败不回滚其它状态保存的 owner 引用或描述符状态；通知中的外部效果同样不回滚。

运算协议在实例类型的原始 C3 MRO 中查找，忽略实例动态字段中的同名函数，随后使用相同的描述符绑定与统一调用机制。算术反向方法可处理另一侧的值；严格子类和拒绝哨兵的顺序见[运算协议](OPERATORS.md)。这些接口由语言显式定义，不根据类名称或自然语言提示猜测运算。

对象可以声明同步或异步迭代工厂和推进方法。运行时把原始实例适配为独立 Iterator/AsyncIterator，保留对象的名义身份和协议分派；活动适配句柄受执行周期限制。普通实例字段中的同名函数不会替代类级特殊协议。详见[用户迭代器](ITERATORS.md)。

条目访问和属性访问是独立协议；同一对象可以让 property x 和 __getitem__("x") 返回不同结果。新方括号遇到完全未声明的条目槽时保留历史成员访问回退，现存但不可调用或抛错的槽不会回退。显式 setitem/delitem 则需要对应协议，不猜测属性写入。见[索引指南](INDEXING.md)。

## 用模式解构组件

`C::{field: value}` 从 C 或子类实例提取有效存储字段，包括 C3 类默认值与实例覆盖。它不会调用 getter／描述符，与只返回实例自有字段的 vars 不同。子类可带额外字段，`..rest` 收集其余存储值。提取遵循所属与权限限制，详见[类字段模式](CLASS_PATTERNS.md)。
