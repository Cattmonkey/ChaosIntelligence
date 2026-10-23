# 用户描述符协议

描述符把一个类成员的读取、写入和删除交给普通 Chaos 方法。它适合验证字段、实例缓存、显式持久化和 Agent 支持的计算属性。协议只在语言自己的 Class／Instance 中运行，不调用宿主 Python 反射；这是明确规定的 Chaos 语义，并不等同完整 Python 元类或描述符生态。

对象基础见[类与实例](OBJECTS.md)，内置 property、staticmethod 和 classmethod 见[装饰器](DECORATORS.md)。

## 一个完整字段

```chaos example=descriptor-storage
class Stored {
  var name = "";
  fn __set_name__(self, owner, name) { self.name = name; null }
  fn __get__(self, instance, owner) {
    if instance == null { self } else { getattr(instance, "_" + self.name) }
  }
  fn __set__(self, instance, value: Int) {
    setattr(instance, "_" + self.name, value); null
  }
  fn __delete__(self, instance) { delattr(instance, "_" + self.name); null }
}
class Meter {
  var _value: Int = 0;
  let value = Stored();
}
let meter = Meter();
meter.value = 42;
let saved = meter.value;
delattr(meter, "value");
[saved, meter.value, Meter.value.name]
```

结果为 `[42,0,"value"]`。`Stored()` 保存在类成员 `value` 上，因而处理 `meter.value`。真正的状态显式存入实例的 `_value`；删除实例的覆盖值后，读取恢复类默认值 0。Class 读取也调用用户 getter，本例通过 `instance == null` 返回描述符自身。

每个描述符实例默认被所有实例共享。不要把每个 Meter 独立的数值直接放进 `Stored` 自己的字段；需要独立状态时，像本例一样放在接收实例上。只读 getter 也可以直接计算返回值。

## 协议与查找顺序

| 协议方法 | 调用含义 | 结果 |
| --- | --- | --- |
| `__get__(self, instance, owner)` | 实例读取；Class 读取时 instance 为 null | 任意语言值，不自动 await 或消费流 |
| `__set__(self, instance, value)` | 实例写入 | 必须同步完成并返回 Null |
| `__delete__(self, instance)` | 实例删除 | 必须同步完成并返回 Null |
| `__set_name__(self, owner, name)` | 类构建完成后的成员命名通知 | 必须同步完成并返回 Null |

协议从描述符实例自身 Class 的 C3 MRO 查找，忽略这个实例后来添加的同名字段。只有类成员中的对象参与访问协议；保存在普通实例自身字段中的描述符值原样返回。只有 `__set_name__` 的对象会得到命名通知，但不因此成为访问描述符。普通 Instance 或 Agent 字段没有这些协议时保持原行为。

在接收对象的 MRO 找到第一个同名类成员后，按以下顺序读取；不会跳过该成员去搜索基类中另一个更强的描述符：

1. 该成员有 `__get__`，且有 `__set__` 或 `__delete__`：先执行 getter。
2. 否则若实例有自身同名字段，读取自身字段。
3. 否则若该成员有 `__get__`，执行 getter。
4. 否则使用通常的方法绑定或原始成员值。

定义 `__set__` 或 `__delete__` 的对象称为 data descriptor。没有 getter 的 data descriptor 不接管读取，仍可读自身字段或原始描述符。写／删 data descriptor 必须具备对应 hook；缺少时抛出 `attribute_error`，不能回退写入实例字段。声明为 let 的 data descriptor 仍可通过自己的 setter 修改业务状态，类成员绑定本身没有因此变成 var。

仅有 getter 的 non-data descriptor 明确允许实例缓存：即使类上是 let，也可以创建同名自身字段，该缓存的契约为 Any。删除缓存后重新使用 getter。普通 let／def 成员仍保持原有只读规则。

```chaos example=descriptor-cache
class Computed { fn __get__(self, instance, owner) { 42 } }
class Report { let answer = Computed(); }
let report = Report();
let original = report.answer;
report.answer = "cached";
let cached = report.answer;
delattr(report, "answer");
[original, cached, report.answer]
```

结果为 `[42,"cached",42]`。没有动态缓存的另一个 Report 实例不受影响。

自定义描述符仍是其名义类的 Instance，`type_of` 不变成 `"Descriptor"`。Descriptor 契约接受拥有 get／set／delete 协议的值，但检查契约不执行 hook；非可调用的协议成员只在实际调用时失败。协议方法自身可以由静态方法、类方法或可调用替换值构成，仍走统一调用和预算检查。

## Class、继承与 super 的 owner

`owner` 是实际访问的 Class，而不是声明字段的基类。实例读取传接收实例；Class 读取传 null。`super(klass, instance)` 从 klass 之后查找，但保持实际派生类作为 owner；以 Class 为接收者时仍传 null。super 不读取实例自身缓存，并且保持只读。

```chaos example=descriptor-owner
class OwnerProbe {
  fn __get__(self, instance, owner) { [instance == null, owner] }
}
class Base { let item = OwnerProbe(); }
class Derived(Base) {}
let instance = Derived();
let a = Derived.item;
let b = instance.item;
let c = super(Derived, instance).item;
let d = super(Derived, Derived).item;
[a[0], a[1] == Derived, b[0], b[1] == Derived,
 c[0], c[1] == Derived, d[0], d[1] == Derived]
```

结果为 `[true,true,false,true,false,true,true,true]`。内置 Property 的 Class 读取保持原有行为，直接返回 Property 值；用户 getter 是否返回自身由用户决定。

## 命名通知与类发布

类先完成所有自身成员，再按最终自身成员的声明顺序调用 `__set_name__`。通知时 owner 已是完整可用的类，hook 可以读取后面声明的成员，也可以构造 owner 的实例。通知使用建立时的名字／值快照；hook 的动态修改不会导致重新扫描。

```chaos example=descriptor-naming
var notices = [];
class Named {
  fn __set_name__(self, owner, name) {
    notices = append(notices, [name, owner().ready]); null
  }
}
let shared = Named();
class Base {
  let first = shared;
  let second = shared;
  var ready = 42;
}
class Derived(Base) {}
notices
```

结果为 `[["first",42],["second",42]]`。同一对象用于两个名字会分别通知；继承不会重复通知。通过后续 `setattr` 添加或替换成员不自动调用命名 hook，需要时可显式调用它。

所有命名 hook 和类装饰器成功后，才发布声明处的公共类名。命名 hook 在类装饰器应用前收到原始 owner，装饰器仍可以替换最终绑定。失败或取消阻止这次公共绑定，但不回滚之前的外部效果、可变状态或已经逃逸的原始 Class 引用；它们不是事务。复用有可变 name 字段的描述符到多个标签时，最后一次通知可能覆盖旧标签，请自行选择不可变映射或为每个标签新建描述符。

## 删除与 property.deleter

`delattr(value, name)` 返回 Null。普通 Instance 删除只移除自身可变／动态字段；继承默认值本身不能通过实例删除。删除 Class 只移除该 Class 自身的可变成员，不调用其中实例描述符的删除 hook，也不删除基类成员。只读成员、缺失成员或只存在于继承链的待删除存储会报错。

```chaos example=descriptor-property-delete
class Meter {
  var stored: Int = 0;
  @property
  fn value(self) { self.stored }
  @value.setter
  fn value(self, item: Int) { self.stored = item; null }
  @value.deleter
  fn value(self) { delattr(self, "stored"); null }
}
let meter = Meter();
meter.value = 42;
let saved = meter.value;
let deleted = delattr(meter, "value");
[saved, deleted, meter.value, vars(meter)]
```

结果为 `[42,null,0,{}]`。`property(fget=null, fset=null, fdel=null)` 的第三参数是删除函数；`.fdel` 可读取它。`.getter(function)`、`.setter(function)`、`.deleter(function)` 都创建新 Property 并保留其它 accessors，沿用同名声明的 Property 更新链规则。缺少删除函数时不允许删除属性。

## 宏、模块与阶段

协议方法和 delattr 都是既有函数／调用语法，没有新增原始 AST／IR 节点。宏可以生成类、方法和 property 更新；成员公开标签保持不变，类名、自身引用与方法参数仍卫生重命名。运行期内建函数与 Agent 需要显式注入封闭模板。

```chaos example=descriptor-macro
macro component(initial, erase) {
  quote {
    class Field {
      fn __get__(self, instance, owner) {
        if instance == null { self } else { instance.stored }
      }
      fn __set__(self, instance, item) { instance.stored = item; null }
      fn __delete__(self, instance) { unquote(erase)(instance, "stored"); null }
    }
    class Component { var stored = unquote(initial); let value = Field(); }
    Component
  }
}
let item = 40;
let Component = component!(item, delattr);
let instance = Component();
instance.value = 42;
let saved = instance.value;
delattr(instance, "value");
[saved, instance.value, item]
```

结果为 `[42,40,40]`。宏只产生语法；编译期不能创建运行期 Class、调用 delattr、执行访问或命名 hook。所有模块先完成宏展开与检查，再执行类构建和 Agent／tool 效果。导出的类保持同一名义身份，导出宏的限定调用和来源规则见[宏规范](MACROS.md)。

## Agent 支持的字段

下面经过实际 Framework、Registry、Context 和异步调度器调用本地确定性提供方，不需要外部模型或密钥。普通 setter／deleter 在当前 Task 中调用 Agent 时可挂起等待；它们仍以 Null 完成。异步 getter 返回冷 Task，显式 await 后才执行。

```python
from chaos import Final, Framework, Registry
from chaos.settings import Settings

registry = Registry()
seen = []

@registry.provider("app.field")
def field_provider(request, context):
    context.checkpoint()
    seen.append(request.input["op"])
    value = request.input.get("value", 0)
    return Final(value * 2 if request.input["op"] == "read" else value)

source = '''
agent worker {provider: "app.field", tools: [], maxSteps: 1}
class RemoteField {
  async fn __get__(self, instance, owner) {
    worker({op: "read", value: instance.stored})
  }
  fn __set__(self, instance, item) {
    instance.stored = worker({op: "write", value: item}); null
  }
  fn __delete__(self, instance) {
    worker({op: "delete"}); delattr(instance, "stored"); null
  }
}
class Component { var stored: Int = 0; let answer = RemoteField(); }
let component = Component();
async fn exercise() {
  component.answer = 21;
  let answer = await component.answer;
  delattr(component, "answer");
  [answer, component.stored, vars(component)]
}
await exercise()
'''
with Framework(registry=registry, settings=Settings(":memory:")) as app:
    project = app.create_project("descriptor-agent", source=source, capabilities=[])
    run = app.start(project["id"], project["version"], None, mode="source")
    assert run.result(timeout=5) == [42, 0, {}]
    events = app.events(project["id"], run_id=run.id, limit=300)["items"]
    assert sum(event["type"] == "agent.completed" for event in events) == 3
assert seen == ["write", "read", "delete"]
```

描述符不会授予新能力，或把实例自动传给提供方。上例传入普通 Record；需要对象业务状态时用 vars 或明确字段构造投影。Framework／Registry、API、异步 worker 都使用 JSON 数据边界；低层同步 Python 回调仍保留受信任的旧活函数 ABI，不能视为隔离沙箱，详见[对象边界](OBJECTS.md)。

## 效果与检查边界

getter 可以返回 Task、Generator 或 AsyncGenerator；没有隐式等待或消费。`__set__`、`__delete__`、`__set_name__` 的显式 async／生成器函数在进入 body 前被拒绝，普通包装函数偷偷返回挂起句柄也违反 Null 契约。hook 共享解释器预算、归属检查和 Context 的权限／取消规则，不形成新的无限递归通道。

直接读取、getattr 和 hasattr 都可能执行 getter；hasattr 仅把 attribute_error 变成 false，其它失败传播。vars、default_dir、类型／可调用能力检查、调试摘要和序列化不执行属性 hook。显式 dir 可调用 __dir__；普通属性钩子位于描述符逻辑之外，可用 default_* 委托本文的默认顺序，见[属性协议](ATTRIBUTE_PROTOCOL.md)。检查界面展示保存的受限状态，因此不会为了展开变量而触发 Agent。调试与资源限制见[调试器](DEBUGGER.md)和[效果规范](EFFECTS.md)。

参考设计来自 [Python 官方描述符指南](https://docs.python.org/3/howto/descriptor.html) 与 [数据模型](https://docs.python.org/3/reference/datamodel.html#implementing-descriptors)。Chaos 自行规定 let／var 存储、Null hook 契约、预算、显式异步与类发布边界；0.66 的 Class 成员读取也处理元类数据描述符优先级，详见[原生元类](METACLASSES.md)；不提供任意 Python 对象访问或隐式自动 await。
