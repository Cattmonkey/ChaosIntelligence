# 属性访问与动态智能体接口

属性协议让普通类和元类定义成员读取、缺失回退、写入、删除与名称目录。它们是语言方法，通过原 AST、效果调度器和能力边界执行；可用来把角色名称解析成真实 Team 函数、为工具组件提供计算属性，或给模型结果提供明确的数据视图。编辑器不根据字符串猜测这些接口，也不为它增加独立面板。

## 五个钩子与默认委托

| 钩子 | 调用时机 | 结果 |
| --- | --- | --- |
| `__getattribute__(self, name)` | 每次普通属性读取，优先于默认描述符／存储逻辑 | 任意合法语言值 |
| `__getattr__(self, name)` | 普通读取整体抛出 `attribute_error` 后，回退一次 | 任意合法语言值 |
| `__setattr__(self, name, item)` | 普通属性写入 | 必须同步完成并返回 null |
| `__delattr__(self, name)` | 普通属性删除 | 必须同步完成并返回 null |
| `__dir__(self)` | 显式语言 `dir(value)` | 同步可迭代的 String 名称 |

钩子按实际类的 C3 MRO 查找，不读取实例上同名字段，不再次调用属性钩子来查找自身。没有声明钩子的对象保持原有行为。`getattr(value,name)`、`hasattr(value,name)`、点访问与旧索引属性回退共用普通读取；赋值、增强赋值与 `setattr(value,name,item)` 共用写入，`delattr(value,name)` 共用删除。条目协议存在时，方括号仍首先采用独立的 `__getitem__`／`__setitem__` 规则。

| 默认委托函数 | 语义 |
| --- | --- |
| `default_getattr(value, name)` | 跳过属性读取／缺失钩子，执行原描述符与存储读取 |
| `default_setattr(value, name, item)` | 跳过写入钩子，执行原描述符与存储写入，返回 item |
| `default_delattr(value, name)` | 跳过删除钩子，执行原描述符与存储删除，返回 null |
| `default_dir(value)` | 原有的有界、排序成员目录，不执行用户属性或目录钩子 |

默认委托不是任意原始字段访问：data descriptor、property、只读声明、字段契约、成员限额、所有权、能力投影与原生类保护仍有效。描述符本身可执行原有授权效果。没有新增隐式 Object 基类，不改变旧 C3 顺序；`super` 保持原来的显式基类访问。避免在读取钩子中用 `self.name` 读取自身状态，这会再次进入钩子；应显式委托。

```chaos example=attribute-five-hooks
class Box {
  var base:Int = 0;
  fn __getattribute__(self, name) {
    if name == "answer" { default_getattr(self, "base") + 2 }
    else { default_getattr(self, name) }
  }
  fn __getattr__(self, name) {
    if name == "missing" { 40 } else { throw AttributeError(name) }
  }
  fn __setattr__(self, name, item) { default_setattr(self, name, item); null }
  fn __delattr__(self, name) { default_delattr(self, name); null }
  fn __dir__(self) { ["base", "answer", "answer"] }
}
let box = Box();
box.base = 40;
let before = box.answer;
let names = dir(box);
delattr(box, "base");
[before, box.missing, names, box.answer]
```

结果是 `[42,40,["answer","answer","base"],2]`。写钩子的返回值必须是 null，但赋值和 setattr 的表达式结果继续为原始 item；删除成功返回 null。删除实例自有字段后，默认读取仍可见类中声明的初始值。

## 缺失、冷任务与目录

只有实际错误码 `attribute_error` 触发缺失回退，包括默认描述符 getter 抛出的该错误；不是检查错误消息文字。`__getattr__` 在捕获范围之外调用，只回退一次。其它普通错误、取消和预算超限直接传播。hasattr 只把最终 attribute_error 转成 false。自动回退期间，原属性错误作为 error_context 因果上下文，结束后恢复；它不激活 current_error 或裸 throw 的动态重抛状态，冷 Task 仍沿既有任务隔离。

```chaos example=attribute-cold-read
var started = 0;
class Proxy {
  async fn __getattr__(self, name) { started += 1; 42 }
}
let pending = Proxy().answer;
let before = started;
[before, type_of(pending), await pending, started]
```

结果是 `[0,"Task",42,1]`。读取可以返回冷 Task／Generator，取到句柄就是成功；不会隐式 await，后来任务失败也不会重新执行缺失回退。写入／删除钩子必须同步，async 或 generator 方法不能作为它们的实现。

目录钩子允许普通同步函数返回同步迭代器，也允许 `fn* __dir__` 直接产生同步 Generator；不启动 async fn／async fn*，不隐式 await Task。每个产生的名称必须是精确 String，保留重复名称并排序；不把结果解析为源码，也不按名称读取成员。最多消费 10,000 个名称（重复项也计数），总名称文本最多 250,000 字符；游标是借用的，验证失败不会自动关闭它。

```chaos example=attribute-directory
class Directory {
  fn* __dir__(self) { yield "worker"; yield "planner"; yield "worker" }
}
dir(Directory())
```

结果是 `["planner","worker","worker"]`。显式 dir 可以有授权效果。IDE 提示、vars、纯类字段模式、隐式特殊方法查找、调试预览和 JSON 描述均不调用这五个钩子；default_dir 保持无用户钩子的名称查询。

## 元类与实际角色代理

Class 值通过其实际元类的五个钩子定制属性；类自身声明的这些方法用于它的实例。元类读取同样先拦截，再可显式委托原描述符／类存储。构造、长度、哈希、运算、迭代等隐式特殊方法仍从真实类型取协议，不被普通属性拦截器替换。

```chaos example=attribute-metaclass
class Meta(Type) {
  fn __getattribute__(cls, name) {
    if name == "answer" { default_getattr(cls, "base") + 2 }
    else { default_getattr(cls, name) }
  }
  fn __getattr__(cls, name) { if name == "extra" { 7 } else { throw AttributeError(name) } }
  fn __setattr__(cls, name, item) { default_setattr(cls, name, item); null }
  fn __delattr__(cls, name) { default_delattr(cls, name); null }
  fn __dir__(cls) { ["answer", "base"] }
}
class Service(metaclass=Meta) { var base:Int = 40 }
Service.base = 41;
let answer = Service.answer;
let names = dir(Service);
delattr(Service, "base");
[answer, Service.extra, names, hasattr(Service, "base")]
```

结果是 `[43,7,["answer","base"],false]`。动态角色代理可在 `__getattr__` 中调用 `team_role(default_getattr(self,"bound"),name)`，返回原生角色函数后由调用者显式 await。代理不复制角色权限，也不把名称当作提示词执行。AgentTool、Code、module_from 和 Program 修改使用同一访问逻辑；源码是可复制包的程序资产，宿主注册本地工具或供应商仍需明确配置。

std/swarm 的快照、prepare 结果和 worker 返回保持有限 JSON；工作函数可以内部操作动态组件、调用真实 Team，再显式投影为状态数据。嵌套返回函数、闭包和接续沿当前接收者／钩子能力交集投影，不恢复已经收窄的 next 权限。失败不撤销已经发生的授权效果。

## 对象访问的执行状态

普通属性钩子、读取／写入／目录、描述符调用解析、原始成员委托、条目键与历史索引、无损整数及条目协议，以及属性／索引／增强赋值的表达式求值，现统一由15种真实对象访问帧推进。目标、名称、键、原值、右值、待决回调、能力、错误上下文和目录游标都是决定后续执行的数据；源码、IR、Code／Program、宏、AgentTool／Team／Bot、CLI／SDK／HTTP 与既有编译回退共用实现。原有求值次序、冷句柄、同步写入、检查点、权限及取消合同不变。类创建／元类内部算法、Set／HashMap 内部算法和其它尚未迁移协议仍保留明确叶边界；这不是保存／复用 continuation、自研 GC、通用 AOT、自举或完整 Python／Lisp 生态已经完成的声明。

读取失败时保留真正的 attribute_error 及其因果上下文；成功、失败、取消和强制关闭后恢复调用者权限与动态错误上下文。目录帧保存真实游标、忙碌锁、已消费名称及文本计数，释放借用锁而不隐式关闭输入。属性／索引增强赋值按目标→键→读取旧值→右值→原位运算→写入的原有顺序推进，已经完成的工具效果不会因恢复而重放。

```chaos
class Position { fn __index__(self) -> Int { 1 } }
class Component {
  var value:Int = 40;
  fn __getattr__(self, name) { if name == "answer" { self.value + 2 } else { throw AttributeError(name) } }
  fn __getitem__(self, key) { default_getattr(self, key) }
  fn __setitem__(self, key, item) { default_setattr(self, key, item); null }
}
let component = Component();
component["value"] += 2;
[component.answer, component["value"], [20, 42][Position()]]
```

结果为 `[44,42,42]`。智能体可以把这一普通 Component 作为工具函数内部组件；它的动态属性与条目调用直接进入同一语言调用和效果系统，而不是从模型文字推断动作。

## 编辑器、接口与边界

源码、格式化、IR、CLI、SDK、HTTP 和工作台沿现有 class／function／index／set_member／call 节点读写。原有 get/has/set/del/dir 签名不变；四个 default 函数使用共享 NativeSpecs 提供参数提示。若静态类声明了 __getattribute__，普通成员读取与补全只给 Any，不把存储方法签名或声明位置当成实际返回值；存在 __getattr__ 时，可抛缺失错误的描述符读取同样保守为 Any；__getattr__／__dir__ 不用于猜测动态成员名。super 与隐式协议保留原始声明元数据。同源可追踪的实际 Class 绑定经成员赋值或真正内建 setter 安装／删除钩子时，会失效本类及继承访问的确定提示；内建函数被遮蔽时不套用此规则。经过未知函数、无法追踪的别名或其它运行时效果修改类，不属于全程序静态效果分析，提示不能替代运行时契约。

完整 Python／Lisp 对象生态、析构器、任意宿主对象反射与无资源上限运行不属于本能力。原有描述符顺序见[用户描述符](DESCRIPTOR_PROTOCOL.md)，类与元类见[对象](OBJECTS.md)和[元类](METACLASSES.md)，能力范围见[对照表](PYTHON_LISP_CAPABILITIES.md)。可直接运行[属性代理示例](../examples/attribute-protocol.chaos)。
