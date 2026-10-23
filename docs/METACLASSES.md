# 原生元类与命名空间

元类是生成 Class 的语言值。智能体组件可以用元类统一安装函数、检查声明和组织角色；这些操作进入 Chaos 原有的 AST 求值、能力范围与调度器。可视化编辑器修改同一份 class.options，CLI、Program、API 与 SDK 沿同一创建管线执行。

类声明、运行期 new_class／Type 构造、基类适配、准备命名空间的读写、元类调用与类型判断，以及函数／类／Agent 装饰器，现在由同一执行游标上的21种真实类型化帧推进。待决字段保存实际命名空间、共享 Cell、成员快照、参数、类壳和构建栈租约，直接决定下一步执行；源码、IR、Code／Program、宏和智能体回调共用这些状态。原有从左到右求值、协议调用顺序、权限上限、检查点、冷 Task、错误和取消合同保持不变。函数形成与泛型方法组合也已使用[共享执行帧](EXECUTION_FRAMES.md#函数形成与泛型方法组合的执行状态)；这没有提供保存或复用 continuation、独立 GC、通用 AOT、自举或完整 Python／Lisp 生态。

## Type、Class 和反射

```chaos example=metaclass-native-identity
class Meta(Type) {}
class Component(metaclass=Meta) {}
class Child(Component) {}
[class_of(Type) == Type, class_of(Meta) == Type,
 class_of(Component) == Meta, class_of(Child) == Meta,
 Type(Component) == Meta, Type(Component()) == Component,
 isinstance(Component, Meta), issubclass(Meta, Type), type_of(Meta)]
```

`Type` 是每次解释执行自己的默认元类，自身的元类也是 Type。普通类的值仍为 Class；`type_of` 保持字符串 `"Class"`，不改变旧接口。`class_of` 和单参数 `Type` 接受 Class 或 Instance，分别返回元类或实际类。0.68 的公开 `isinstance`／`issubclass` 调用实际元类的同步 Bool 钩子；默认 `Type.__instancecheck__`／`Type.__subclasscheck__` 保持名义判断，见[类型协议](TYPE_PROTOCOLS.md)。这里没有调用任意名称字符串或宿主 Python 类型。

内置 `Class` 契约接受全部原生 Class；`Type` 契约要求继承原生 Type 的元类。实际元类作为名义契约时接受它所创建的 Class。标签数据定义仍为 DataType，与默认元类分开。

## 在语言里改造类创建

```chaos example=metaclass-transform-members
class Meta(Type) {
    fn __prepare__(mcls, name: String, bases: List, **options) {
        {}
    }
    fn __new__(mcls, name: String, bases: List, namespace, **options) {
        Type.__new__(mcls, name, bases,
            merge(namespace, {stamp: class_member(40)}), **options)
    }
}
class Component(metaclass=Meta) {
    fn __call__(self, value: Int) -> Int { value + self.stamp }
}
let Dynamic = new_class("Dynamic", [], class_members(Component), {metaclass: Meta});
[Component()(2), Dynamic()(2), class_of(Component) == Meta,
 class_members(Component).stamp.value]
```

普通命名 `__prepare__` 隐式绑定实际元类，也支持显式 classmethod。`__new__` 按静态方法调用，显式接收元类。元类普通方法的接收者是它创建的 Class，因此可用 `__init__` 初始化类、用 `__call__` 改造类的调用。

class 声明与 `new_class` 是高层创建入口。基类及选项求值一次，选择能兼容全部基类元类的最派生元类，执行 prepare，填充命名空间，再调用元类。默认 `Type.__new__` 安装成员并按原先顺序执行 `__set_name__`、协作 `__init_subclass__`；元类初始化完成后才执行类装饰器并发布名称。失败时不发布外层绑定，已发生的效果不回滚。

`Type(name, bases, namespace, **options)`、`Type.__new__(mcls, name, bases, namespace, **options)` 是低层入口，使用给定命名空间，不再调用 prepare。不应在元类 new 中再调用高层 `new_class` 来替代默认分配，否则可能再次进入自己的 prepare／new。

## 命名空间合同

prepare 返回普通 Record，或本解释器原生 Instance。自定义命名空间提供同步 `keys()`、`__getitem__`、`__setitem__`，键为唯一 String。类名须为非空 String，且不能重定义内置类型；在 prepare、工厂和命名空间效果前校验。命名空间键也接受共享节点、String 总量和所有权预算。类体的写入包含 ClassMember，保留 let／var、契约和方法标记；不能通过值看起来像函数就猜测它应绑定接收者。默认低层创建复制命名空间，不共享类可变成员 Cell。Record 准备结果先复制再写入；原生映射每次类体名称读取都执行实际 __getitem__，仅真正缺键错误回退到外层词法绑定，因此自定义 setter 对其他键的修改立即可见。方法闭包保持外层词法环境和只读的最终类引用，不捕获整个 prepared 映射；源码不能给内部类自引用赋值。

内置 Type 本身不能修改或删除成员；元类实例必须经 Type.__new__ 创建真实 Class，alloc_instance 不接受 Type 子类。prepare、元类创建及初始化必须同步完成，不隐式等待 Task 或推进生成器。这些原生函数仍可调用原有工具效果，接受能力、取消及预算管理。普通 `Component()` 的自定义元类 `__call__` 则可以显式返回 Task／Generator，由调用方决定 await 或推进。

## 显式工厂和返回值

```chaos example=metaclass-callable-factory
fn factory(name: String, bases: List, namespace, **options) {
    let Generated = Type(name, bases, namespace, **options);
    fn(input: Int) -> Int { input + Generated.offset }
}
class Component(metaclass=factory) { let offset: Int = 40 }
let Dynamic = new_class("Dynamic", [], {offset: 40}, {metaclass: factory});
[Component(2), Dynamic(2), type_of(Component)]
```

显式 metaclass 也可以是本解释器的原生可调用工厂，其调用由原有语言函数规则执行。工厂或自定义元类 new 可以返回合法的非 Class 值，此时 class 名字绑定实际返回值；不会把它强制包成应用层对象。工厂可以是 Function、原生可调用 Instance／Class、StaticMethod／BoundMethod 或原生 Native；不接受宿主可调用对象。工厂不执行 Type 子类的最派生选择。同步创建不接受未执行的 Task／Generator。

因此 `new_class`、多参数 `Type` 和 `Type.__new__` 的通用返回提示为 Any；低层最派生元类也能返回其它原生值。单参数 Type 的成功查询结果仍是 Class。IDE 不运行用户 prepare／new／call 推断结果，自定义元类和工厂的创建结果也保守提示 Any。调试器显示有界的元类名称，不访问用户 getter 或把活闭包导出为可恢复 JSON。

## 生成智能体角色

```chaos example=metaclass-agent-role
agent brain {provider: "local", tools: []}
fn plus(input: Record) -> Int { input.value + 2 }
class RoleMeta(Type) {
    fn __new__(mcls, name, bases, namespace, **options) {
        Type.__new__(mcls, name, bases,
            merge(namespace, {brain: brain, handler: plus}), **options)
    }
}
class Role(metaclass=RoleMeta) {
    fn __call__(self, input: Record) -> Int { self.handler(input) }
}
let component = Role();
let tool = agent_tool("role", component, input_type="Record", output_type="Int");
let equipped = agent_bind(component.brain, [tool]);
[component({value: 40}), agent_tool_info(tool).name, type_of(equipped({value: 40}))]
```

示例只创建冷任务，不调用模型。实际工具执行仍沿原生 AgentTool、函数、权限和取消合同。Program 可生成／验证／采用包含这些元类的源码，活动运行继续使用原版本快照。

此机制补齐语言中的元类、prepare 与创建工厂；不代表已实现全部 Python／Lisp 功能。通用 continuation、完整数值塔、标准库、编译后端与自研 GC 等剩余范围见[能力对照](PYTHON_LISP_CAPABILITIES.md)。

当前 Type 反射范围为 Class／Instance，标量内置值尚未全部统一为 Type 实例；class 体仍限 let／var／方法声明，不是任意语句块。0.68 已提供[类型判断与基类适配](TYPE_PROTOCOLS.md)：公开元类判断、一次原始基类展开和 postbody __orig_bases__；内部名义身份保持独立。

设计参照：[Python 类创建](https://docs.python.org/3/reference/datamodel.html#customizing-class-creation)、[PEP 3115](https://peps.python.org/pep-3115/)。本语言保持自身 let／var、ClassMember、严格 Bool、词法闭包、所有权与效果规则，不运行 Python 源码。
