# 可编程类创建

类创建是语言运行时的一条管线：源码声明和动态函数创建的类共享 C3、成员契约、描述符通知、继承通知与调用入口。它允许程序生成智能体组件和角色工厂，不依靠模型输出中的关键词判断动作，也不在网页另做一套对象系统。

类声明、运行期 new_class／Type 构造、基类适配、准备命名空间的读写、元类调用与类型判断，以及函数／类／Agent 装饰器，现在由同一执行游标上的21种真实类型化帧推进。待决字段保存实际命名空间、共享 Cell、成员快照、参数、类壳和构建栈租约，直接决定下一步执行；源码、IR、Code／Program、宏和智能体回调共用这些状态。原有从左到右求值、协议调用顺序、权限上限、检查点、冷 Task、错误和取消合同保持不变。函数形成与泛型方法组合也已使用[共享执行帧](EXECUTION_FRAMES.md#函数形成与泛型方法组合的执行状态)；这没有提供保存或复用 continuation、独立 GC、通用 AOT、自举或完整 Python／Lisp 生态。

## 动态函数生成组件

```chaos example=class-creation-dynamic
class Named {
    var kind: String = "";
    fn __init_subclass__(cls, kind: String = "", **options) {
        super(Named, cls).__init_subclass__(**options);
        cls.kind = kind;
        null
    }
}
let members = {
    value: class_member(0, mutable=true, contract="Int"),
    __init__: class_member(fn(self, start: Int = 0) {
        self.value = start;
        null
    }, method=true),
    __call__: class_member(fn(self, step: Int = 1) -> Int {
        self.value = self.value + step;
        self.value
    }, method=true)
};
let Role = new_class("Role", [Named], members, {kind: "worker"});
let Mirror = new_class("Mirror", [Named], class_members(Role), {kind: "mirror"});
let worker = Role(40);
worker(2);
Role.value = 10;
[worker.value, Mirror.value, class_members(Role).value.contract,
 class_members(Role).__call__.method, Role.kind]
```

结果为 `[42,0,"Int",true,"worker"]`。普通成员值是只读 Any 字段；`class_member` 明确给出可写性、契约和是否绑定接收者，不根据函数名或值的外观猜测。`method=true` 需要原生 Function 和首个位置接收者参数，可为普通、异步或生成器函数，不能同时 `mutable=true`。Agent、Program 和工具绑定可作为字段保存，以自身参数规则调用。

`class_members` 提取类自身成员的有序元信息快照，不含继承成员，不执行 getter／描述符。重建类会新建 Member 和可写 Cell；原始函数的闭包不改写，成员引用的其它实例仍按已有默认值规则共享。这不是深克隆，也不会给生成函数插入一个新的自由变量类名。vars 的自有状态投影同样保留能力上界，不能借反射取得更宽权限的函数。

## 继承通知与声明参数

```chaos example=class-creation-subclass
class Named {
    var label: String = "";
    fn __init_subclass__(cls, label: String = "", **options) {
        super(Named, cls).__init_subclass__(**options);
        cls.label = label;
        null
    }
}
class Reader(Named, label="reader") {}
let Writer = new_class("Writer", [Named], {}, {label: "writer"});
[Reader.label, Writer.label]
```

结果为 `["reader","writer"]`。头部可以使用具名参数和 `**Record`，每个表达式按源码顺序求值一次，重复参数会失败。它们交给沿新类 C3 从自身之后找到的第一个 `__init_subclass__`；不会把每个父类各调用一次。普通命名 fn 的此协议隐式绑定实际派生 Class，显式 classmethod 也可使用。各层取出自己的参数，再用 `super(Base, cls)` 转交剩余参数。继承通知必须同步完成并返回 null，不隐式等待 Task 或推进生成器。末端默认协议只接受空参数，防止拼错配置被静默忽略。

命名通知 `__set_name__` 按自身成员的固定快照先执行，然后运行继承通知，最后才运行类装饰器并发布外层名字。失败时不发布最终绑定；已保存出去的类引用和已经发生的效果不会回滚。通知通过原求值器执行，继承能力范围、步数、取消及错误合同。

## 分配与初始化

```chaos example=class-creation-allocation
class Cached {
    var saved: Any = null;
    var value: Int = 0;
    fn __new__(cls, value: Int) {
        if cls.saved == null { cls.saved = alloc_instance(cls) } else {};
        cls.saved
    }
    fn __init__(self, value: Int) {
        self.value = value;
        null
    }
}
let first = Cached(10);
let second = Cached(20);
class Answer {
    fn __new__(cls) { 42 }
    fn __init__(self) { throw "must not initialize" }
}
[first == second, first.value, second.value, Answer()]
```

结果为 `[true,20,20,42]`。`__new__` 是同步分配协议，普通命名 fn 隐式按静态方法读取，构造时显式传入请求的 Class 与原调用参数。`alloc_instance` 只分配已完成的本解释器 Class 实例，不调用 new 或 init，实际分配计入实例预算。

返回请求类或其子类的实例后，运行返回对象**实际类**的同步 `__init__`，仍要求 null。缓存实例再次返回也会再次初始化；程序可以自行使初始化幂等。返回其它正常值时跳过初始化，可直接生成函数、数据或另一个合法对象；返回值也受构造类的能力上界约束，函数保留原生结构并收窄权限，无法安全投影的活对象明确失败。不会隐式等待 Task 或推进生成器。没有 new 的旧类继续默认分配并运行原构造器。0.64 起 __new__ 和 __init_subclass__ 成为上述语言协议；此前若用这两个名字编写无关的普通方法，需要改名或遵守新协议合同。

## 统一接口与元信息

| 原生函数 | 行为 |
| --- | --- |
| `class_member(value, mutable=false, method=false, contract="Any")` | 创建不可变、受解释器所有权约束的 ClassMember；Bool 参数必须精确为 Bool |
| `class_members(klass)` | 提取自身成员元信息快照，每项保存 value／mutable／method／contract |
| `new_class(name, bases=[], members={}, options={})` | 从有序 String Record 走元类／工厂高层创建；默认结果 Class，自定义创建可返回其它值；ClassMember 保留显式声明合同 |
| `alloc_instance(klass)` | 仅分配实例，复用所有权、类完成状态、能力范围和分配预算 |

contract 接受内置类型字符串、真实 Class／DataType 或[原生组合 Contract](CONTRACTS.md)；Class 和 Contract 必须属于当前解释器，组合中的名义子契约仍按实际所有权验证。不将任意字符串再次当自由环境变量查找；Contract 以不暴露活子句柄的不可变描述保存，外部诊断不恢复类身份或权限。ClassMember 字段只读；读取其活值仍执行所有权及能力投影检查，不能通过元信息封装提高权限。被收窄的函数保留原生函数结构与闭包；无法安全投影的更宽权限活对象明确失败。

动态工厂在执行任何描述符或继承 hook 前检查成员、基类、选项与 C3。限制沿用基类最多 32 个、成员最多 10000 项、MRO 最多 256 项、每次执行类创建最多 1000 个与实际实例分配最多 10000 个；也受统一计算预算约束。ClassMember 通过宿主边界产生有界描述，不能从 JSON 恢复活动函数或对象。

源码、Program、宏、格式化、结构编辑、CLI、SDK、HTTP 和 IDE/LSP 使用同一 AST 和调用合同。没有头部参数的原 class AST 和已保存 IR 保留原形；有参数时新增可选 options 字段。语言服务不会为了推断类型执行创建 hook；自定义实例 new、元类 new／call 或显式工厂可以返回其它值，因此创建和构造结果采取保守类型提示。类体目前仍限 let／var／方法声明；不执行任意语句块，标量内置值也没有全部统一为 Type 实例。

## 作为智能体工具

```chaos example=class-creation-agent-component
fn plus(input: Record) -> Int { input.value + 2 }
agent worker {provider: "local", tools: []}
let Role = new_class("Role", [], {
    brain: worker,
    handler: plus,
    __call__: class_member(fn(self, input: Record) -> Int {
        self.handler(input)
    }, method=true)
});
let component = Role();
let role_tool = agent_tool("role", component, input_type="Record", output_type="Int");
let equipped = agent_bind(component.brain, [role_tool]);
[component({value: 40}), agent_tool_info(role_tool).name,
 type_of(equipped({value: 40}))]
```

结果为 `[42,"role","Task"]`；这里只创建冷任务，不调用模型。真正的结构化工具调用沿同一语言调度器执行 component；测试使用确定性 Provider，见[函数工具](FUNCTION_TOOLS.md)。Program 可以生成和测试组件源码、采用新的版本，活动闭包保持本次执行快照，参见[自编程](SELF_PROGRAMMING.md)。

## 原生元类与后续边界

0.66 加入每个解释器的原生 Type、真正元类选择、prepare 命名空间、元类属性／调用分派和显式可调用创建工厂；`metaclass` 使用原 class.options 顺序 AST，并非网页端配置关键字。class 声明与 new_class 共享高层创建；Type／Type.__new__ 是不再 prepare 的低层分配入口。具体同步、命名空间和返回值合同见[原生元类](METACLASSES.md)。完整 Python／Lisp 能力仍需要通用 continuation、数值塔、完整标准库及后端，见[能力对照](PYTHON_LISP_CAPABILITIES.md)。

设计对照：[Python 类创建数据模型](https://docs.python.org/3/reference/datamodel.html#customizing-class-creation)、[PEP 487](https://peps.python.org/pep-0487/)。本语言保持自己的 let／var、严格 Bool、词法绑定、效果与所有权规则，不直接运行 Python 文本。

0.68 的[类型判断与基类适配](TYPE_PROTOCOLS.md)让原生元类公开判断组件接口，并用真实函数选择 Class 基类。源码和 new_class 共享一次展开与 postbody 原始基类元数据；低层 Type 仍只接受 Class，内部契约、模式及权限保持名义边界。
