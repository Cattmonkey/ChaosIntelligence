# 装饰器、静态方法与属性

装饰器是定义时执行的普通语言调用。它接收刚创建的函数、类或 Agent，返回该名称最终绑定的值；不要求模型解释注解，也不会在宏展开期间执行。`staticmethod`、`classmethod` 和 `property` 是本语言的显式描述符值，不转发到 Python 反射协议。

类声明、运行期 new_class／Type 构造、基类适配、准备命名空间的读写、元类调用与类型判断，以及函数／类／Agent 装饰器，现在由同一执行游标上的21种真实类型化帧推进。待决字段保存实际命名空间、共享 Cell、成员快照、参数、类壳和构建栈租约，直接决定下一步执行；源码、IR、Code／Program、宏和智能体回调共用这些状态。原有从左到右求值、协议调用顺序、权限上限、检查点、冷 Task、错误和取消合同保持不变。函数形成与泛型方法组合也已使用[共享执行帧](EXECUTION_FRAMES.md#函数形成与泛型方法组合的执行状态)；这没有提供保存或复用 continuation、独立 GC、通用 AOT、自举或完整 Python／Lisp 生态。

## 定义与调用顺序

每个 `@expression` 独占一个逻辑行，后面紧跟函数、类或 Agent 声明。可以连续写多个装饰器；括号或方括号内部允许换行。普通、异步、生成器和异步生成器函数均可装饰，类体内只允许装饰方法。

```chaos example=decorators-order
var order = [];
fn mark(label) {
  order = append(order, "expression:" + label);
  fn(value) {
    order = append(order, "apply:" + label);
    value
  }
}
@mark("first")
@mark("second")
fn answer() { 42 }
[order, answer()]
```

结果为 `[["expression:first","expression:second","apply:second","apply:first"],42]`。装饰器表达式从上到下求值，随后求值原声明的默认参数、基类或 Agent 配置，最后从下到上调用装饰器。表达式使用定义前的环境；新名称只在全部成功后发布。若装饰器失败，该声明不发布，但先前已经发生的效果不会回滚。

返回值可以替换为任意语言值，不保证仍是函数、类或 Agent。导出的是最终替换值。原始函数的递归闭包和原始类的自身类型引用仍保持原始身份，不自动改为外层包装值。当前 `rec` 组语法不接受装饰器，可显式用高阶函数组合。

装饰器表达式没有外围 return、循环控制或 yield 目标；await 许可取决于声明位置。普通 fn 内不能通过装饰器绕过 await 限制。装饰器自身的调用与其它调用共享步数、时间、取消和能力边界。

```chaos example=decorators-function
fn doubled(target) {
  fn(value) { target(value) * 2 }
}
@doubled
fn addOne(value: Int) -> Int { value + 1 }
addOne(20)
```

结果为 `42`。包装函数的签名和返回契约由包装函数自己声明；装饰器不会自动复制原签名。

## 静态方法与类方法

```chaos example=decorators-methods
class Base {
  @staticmethod
  fn twice(value) { value * 2 }
  @classmethod
  fn identity(cls) { cls }
}
class Derived(Base) {}
[Base.twice(21), Derived().twice(value=21),
 Derived.identity() == Derived, super(Derived, Derived).identity() == Derived]
```

结果为 `[42,42,true,true]`。静态方法不插入接收者，因此可以没有参数。类方法插入实际访问的类；通过派生类实例或 super 访问时，接收者仍是派生类。`super(klass, instance)` 的 instance 参数可以是相应子类的实例或 Class。

`staticmethod(value)` 和 `classmethod(value)` 接受可调用值。静态方法描述符也可直接调用；类方法描述符必须通过对象成员绑定后调用。读取静态方法返回原可调用值。

普通被装饰的方法只在最终结果是普通语言 Function 值时自动绑定实例。若装饰器返回 Agent、Native 或其它可调用对象，保留它自己的调用参数；需要明确的绑定形式时使用描述符。装饰器返回的四种 Function 形式都保持各自普通／异步／生成器语义。

## 属性读取、写入与删除

```chaos example=decorators-property
class Meter {
  var stored: Int = 0;
  @property
  fn value(self) { self.stored }
  @value.setter
  fn value(self, item: Int) { self.stored = item; null }
}
let meter = Meter();
meter.value = 42;
[meter.value, getattr(meter, "value"), vars(meter)]
```

结果为 `[42,42,{stored:42}]`。实例读取调用 getter，写入调用 setter；属性优先于实例同名字段。Class 读取返回 Property 描述符本身，便于显式复用。缺少对应 getter、setter 或 deleter 时抛出 `attribute_error`。

`property(fget=null, fset=null, fdel=null)` 构造描述符；`.getter(function)`、`.setter(function)` 和 `.deleter(function)` 返回保留其它 accessors 的新描述符，不修改旧值。同名方法重声明仅在原有自有成员是 Property，且新结果沿该 Property 的更新链产生时允许。普通重复方法、重新创建的无关 Property 不因此获准。继承属性可通过基类描述符显式复制，在子类自己的成员声明中更新。

setter 与 deleter 必须同步完成并返回 Null；显式 async／生成器 setter 或 deleter 会被拒绝。普通 setter 返回隐藏的 Task 或流也会违反 Null 契约，不会自动调度它。普通 setter 在已有异步任务内调用 Agent 时，效果仍由调度器挂起等待。成员赋值和 `setattr(value, name, item)` 的结果保留所赋的 item。

```chaos example=decorators-async-property
class Answer {
  @property
  async fn value(self) { 42 }
}
let answer = Answer();
let pending = answer.value;
[type_of(pending), await pending]
```

结果为 `["Task",42]`。异步 getter 返回冷 Task，需要显式 await；读取属性不隐式等待。生成器 getter 同样返回流句柄，按通常的流协议消费。

`getattr`、直接成员读取和 `hasattr` 的默认路径可执行 getter，外层属性钩子可显式委托或替换读取。`hasattr` 只把 `attribute_error` 转为 false，普通错误、资源限制与取消仍传播。`default_dir`、`vars`、调试摘要和序列化不执行属性钩子；它们查看已经保存的受限状态，不能把显示操作变成用户代码执行。显式语言 `dir` 可以执行 __dir__；属性拦截与默认委托见[属性协议](ATTRIBUTE_PROTOCOL.md)。

`@value.deleter` 可像 setter 一样更新同名 Property；通过 `delattr(instance, "value")` 调用删除函数并返回 Null。`.fdel` 保留删除函数。完整可执行示例与普通字段删除规则见[描述符删除](DESCRIPTOR_PROTOCOL.md#删除与-propertydeleter)。

## 宏生成装饰声明

```chaos example=decorators-macro
macro make(decorator, value) {
  quote {
    @unquote(decorator)
    fn answer(extra: Int = 2) { unquote(value) + extra }
    answer
  }
}
fn identity(value) { value }
let extra = 40;
let answer = make!(identity, extra);
[answer(extra=2), extra]
```

结果为 `[42,40]`。宏模板中的声明名、形参和局部绑定卫生重命名，对外具名参数和类成员标签保持稳定。装饰器表达式在新声明绑定之前解析，显式 unquote 的调用者名称不会被模板形参捕获。同名 property accessor 更新可以读取此前保存的公开 Property 成员。

宏纯求值不能执行 decorated 声明；quote 只生成它们的 AST，随后才在运行期执行。原始源码／IR 保留装饰器表达式、顺序和声明，不用文本替换。模块导出检查取原始声明名称，运行时导出该名称的最终装饰结果。更多阶段与来源规则见[宏规范](MACROS.md)。

## 与智能体组合

下面通过实际 Registry 提供方执行被装饰的 Agent，完全使用本地确定性回调，无需外部模型连接：

```python
from chaos import Final, Framework, Registry
from chaos.settings import Settings

registry = Registry()

@registry.provider("app.double")
def double(request, context):
    context.checkpoint()
    return Final(request.input * 2)

source = '''
var calls = 0;
fn observed(target) {
  fn(value) { calls = calls + 1; target(value) }
}
@observed
agent worker {provider: "app.double", tools: [], maxSteps: 1}
class Component {
  let action = worker;
  async fn __call__(self, value) { self.action(value) }
}
let component = Component();
let results = await gather([component(20), component(21)]);
[results, calls]
'''
with Framework(registry=registry, settings=Settings(":memory:")) as app:
    project = app.create_project("decorated-agent", source=source, capabilities=[])
    run = app.start(project["id"], project["version"], None, mode="source")
    assert run.result(timeout=5) == [[40, 42], 2]
```

包装器统计调用，Agent 仍通过 Context、已注册提供方、能力检查与总预算执行。装饰器、属性和方法不会授予额外能力，也不会自动把实例状态发送给模型；需要状态时显式投影为普通业务数据。

## 当前边界

三个描述符的 `type_of` 都是 `"Descriptor"`，可以使用 Descriptor 契约。描述符和绑定接收者受解释器归属检查；描述符在外部数据树中转换为有界描述，不执行 getter，也不传递活描述符。低层同步 Python 回调仍是受信任扩展，旧活函数 ABI 的兼容边界见[对象规范](OBJECTS.md)。

每个声明最多 64 个装饰器；描述符调用沿用统一预算。实例初始化仍要求普通同步 `fn __init__` 并返回 Null；分配与继承通知见[类创建协议](CLASS_CREATION.md)，元类创建和调用见[原生元类](METACLASSES.md)。用户自定义 `__get__`／`__set__`／`__delete__`／`__set_name__`、实例缓存及 `delattr` 见[用户描述符协议](DESCRIPTOR_PROTOCOL.md)。本语言不提供任意 Python 对象访问或所有 Python 装饰器生态兼容。实例与继承基础见[对象规范](OBJECTS.md)，挂起与清理见[效果规范](EFFECTS.md)。
