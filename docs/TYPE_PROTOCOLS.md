# 原生类型判断与基类适配

0.68 将类型判断和基类适配接入原有语言求值器。元类、代理及回调都是本语言的值，使用同一 AST、词法闭包、效果、能力、调度器与预算；不调用宿主 Python 的类型系统。智能体组件可通过这些协议描述公开接口或选择实现类，而实际角色权限仍由原有 Team／Agent 绑定决定。

## 公开类型判断

| 函数或方法 | 合同 |
| --- | --- |
| `isinstance(value, klass)` | 从 klass 的实际元类 MRO 选择 __instancecheck__ |
| `issubclass(child, base)` | child 与 base 必须是已完成的 Class；从 base 的实际元类选择 __subclasscheck__ |
| `Type.__instancecheck__(cls, value)` | 默认名义实例判断 |
| `Type.__subclasscheck__(cls, child)` | 默认名义继承判断 |

公开判断始终调用选中的元类钩子，包括对象恰好属于目标类、child 与 base 相同的情况。钩子必须同步返回严格 Bool；Int、Null、NotImplemented、Task、Generator 均不作为真假值接受，不隐式 await。async／生成器函数作为钩子会在启动其函数体之前被拒绝。普通 fn 不能写 await；需要异步智能体结果时，在外层顶层／async fn 中显式 await，再把所得值传入公开判断。同步函数的工具效果、错误、取消和资源限制仍按原语言规则处理。

```chaos example=type-protocol-predicate
class PositiveMeta(Type) {
  fn __instancecheck__(cls, value) {
    type_of(value) == "Int" && value > 0
  }
  fn __subclasscheck__(cls, child) {
    Type.__subclasscheck__(cls, child)
  }
}
class Positive(metaclass=PositiveMeta) {}
[isinstance(42, Positive), isinstance(-1, Positive),
 issubclass(Positive, Positive), Type.__instancecheck__(Positive, 42)]
```

结果为 `[true,false,true,false]`。类自身的同名成员不能覆盖元类的判断钩子。普通方法接收目标 Class；classmethod 接收实际元类；staticmethod 不添加接收者。property 和用户描述符也按已有绑定协议运行。

## 公开判断与实际身份

虚拟判断成功不改变实际继承、类身份或权限。变量／参数／返回契约、类字段模式、super 的接收者检查、C3、元类选择、分配及初始化均使用内部名义判断，不运行公开判断钩子。`class_of` 和单参数 `Type` 继续返回 Class／Instance 的实际类型。

```chaos example=type-protocol-nominal
class AcceptMeta(Type) {
  fn __instancecheck__(cls, value) { true }
  fn __subclasscheck__(cls, child) { true }
}
class Virtual(metaclass=AcceptMeta) {}
class Actual {}
let value = Actual();
[isinstance(value, Virtual), issubclass(Actual, Virtual),
 match value { Virtual::{} => true, _ => false },
 class_of(value) == Actual,
 Type.__instancecheck__(Virtual, value)]
```

结果为 `[true,true,false,true,false]`。可用 `super(MyMeta, cls).__instancecheck__(value)` 进行协作式名义回退；该 super 操作的合法性仍由真实元类继承决定。

## 高层基类适配

class 声明和 `new_class(name,bases,members,options)` 共用一次基类解析。现有 bases List 接口不变：已完成的 Class 原样保留；拥有 __mro_entries__ 成员的原生 Instance 可作为代理。Class 自身声明这个方法不会触发适配。

每个代理按原列表从左到右调用一次 `proxy.__mro_entries__(original)`。original 是包含全部原始基类的同一个 Tuple。方法必须同步返回精确 Tuple，其元素只能是已完成且属于本求值器的 Class。空 Tuple 删除该代理，多项 Tuple 展开多个实际基类。展开只进行一层；返回代理、List、Task 或生成器均失败。展开前后最多 32 个基类，随后执行重复身份检查、C3 和最派生元类选择；MRO 最多 256 项。

```chaos example=type-protocol-adapter
class Storage { let answer = 42 }
class StorageChoice {
  fn __mro_entries__(self, original) { (Storage,) }
}
let choice = StorageChoice();
class Component(choice) { let label = "source" }
let Dynamic = new_class("Dynamic", [choice]);
[Component.answer, Dynamic.answer, issubclass(Component, Storage),
 Component.__orig_bases__ == (choice,), Dynamic.__orig_bases__ == (choice,)]
```

结果为 `[42,42,true,true,true]`。代理成员查找遵循原来的实例字段、property 和用户描述符优先级，不按照名称或函数体文本猜测返回值。

为了保留原有类头部错误顺序，源码逐项求值并先做无效果的基类存在性／拥有权／重复 Class 检查。全部原始表达式成功后，执行适配和最终 C3；之后才求值具名类选项，再执行 prepare、类体及元类创建。不支持的原始基类或重复 Class 会在选项和类体效果之前失败。这个顺序是 Chaos 的既有合同，并非完整复刻 Python 类头部求值顺序。

## 原始基类元数据及低层分配

只有实际使用了代理替换，才自动写入 `__orig_bases__`。写入发生在源码类体或 new_class 的 members 写入完成之后、调用元类或工厂之前；覆盖类体同名声明。自定义 prepare 映射的真实 set/get 协议会处理这个写入，元类能够观察它。全 Class 基类不自动新增该成员。

```chaos example=type-protocol-factory
class Backend { let answer = 42 }
class Selection { fn __mro_entries__(self, original) { (Backend,) } }
let selected = Selection();
fn build(name, bases, namespace) {
  Type(name, bases, set(namespace, "configured",
    namespace.__orig_bases__ == (selected,)))
}
class Product(selected, metaclass=build) { let __orig_bases__ = null }
[Product.answer, Product.configured, Product.__orig_bases__ == (selected,)]
```

结果为 `[42,true,true]`。`Type(name,bases,namespace)` 与 `Type.__new__(meta,name,bases,namespace)` 是低层入口：只接受已经解析的 Class List，不调用 __mro_entries__，也不自动注入原始基类。元类创建、工厂返回值及 prepare 的原规则见[原生元类](METACLASSES.md)。

自动元数据仍是实际语言值，不能借它把更宽权限的代理、类或闭包带出受限范围。回调执行受调用者、目标类／代理、元类和回调自身能力共同约束；不同执行上下文的活值被拒绝。失败不发布源码类名，已经发生的外部效果不回滚。

完整可运行源码：[type_protocols.chaos](../examples/type_protocols.chaos)，运行 `chaos run examples/type_protocols.chaos` 得到 `[42,42,true,false,true,true]`。

## IDE、图形编辑和兼容范围

源码、结构 IR、图形编辑、CLI、SDK 和 HTTP 使用同一语法与执行路径。Type 方法提供签名、颜色、提示和格式化；协议方法是普通函数节点。IDE 不执行描述符、类型判断或代理函数来猜测结果；不能静态确定展开基类及元类时，保守显示 Any，不编造 MRO。图形修改会转回同一个源码 AST 后运行。

设计参考 [Python 数据模型](https://docs.python.org/3.13/reference/datamodel.html#resolving-mro-entries) 与 [types.new_class](https://docs.python.org/3.13/library/types.html#types.new_class)。Chaos 明确选择严格 Bool 和始终调用公开判断钩子；CPython 的 exact-instance 快路径及一般真值转换与此不同。这里不提供 Python 源码兼容、任意宿主类、标量 Type 全统一或全部 Python／Lisp 功能。剩余范围见[能力对照](PYTHON_LISP_CAPABILITIES.md)。
