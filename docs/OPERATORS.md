# 对象运算协议

Chaos 的对象运算协议让普通表达式调用语言类的方法，方法内部仍可组合 Agent、工具、描述符和 Task。普通表达式复用 unary／binary／call AST，增强赋值使用 aug_assign AST，不使用模型猜测操作含义，也不转发到宿主 Python 运算。原始源码、结构 IR 和模块导出继续保存用户写下的程序。

## 数值对象与运算表

```chaos example=operators-value
class Amount {
  var value: Int = 0;
  fn __init__(self, value: Int) { self.value=value; null }
  fn __add__(self, other) {
    if isinstance(other, Amount) { Amount(self.value+other.value) }
    else { NotImplemented }
  }
  fn __radd__(self, other: Int) { Amount(other+self.value) }
  fn __neg__(self) { Amount(-self.value) }
  fn __abs__(self) { abs(self.value) }
}
[(Amount(20)+Amount(22)).value, (2+Amount(40)).value,
 (-Amount(42)).value, abs(Amount(-42))]
```

结果为 `[42,42,-42,42]`。二元操作数先从左到右各求值一次，再决定调用哪个协议。传入类型不受支持时返回 NotImplemented；如果方法主动抛错，错误直接传播，不把失败当成要求尝试反向运算。

| 表达式或全局函数 | 正向协议 | 反向协议 |
| --- | --- | --- |
| `left + right` | `__add__(self, other)` | `__radd__(self, other)` |
| `left - right` | `__sub__(self, other)` | `__rsub__(self, other)` |
| `left * right` | `__mul__(self, other)` | `__rmul__(self, other)` |
| `left / right` | `__truediv__(self, other)` | `__rtruediv__(self, other)` |
| `left % right` | `__mod__(self, other)` | `__rmod__(self, other)` |
| `base ** exponent` / `pow(base, exponent)` | `__pow__(self, other)` | `__rpow__(self, other)` |
| `floordiv(left, right)` | `__floordiv__(self, other)` | `__rfloordiv__(self, other)` |
| `left & right` | `__and__(self, other)` | `__rand__(self, other)` |
| `left \| right` | `__or__(self, other)` | `__ror__(self, other)` |
| `left ^ right` | `__xor__(self, other)` | `__rxor__(self, other)` |
| `left << right` | `__lshift__(self, other)` | `__rlshift__(self, other)` |
| `left >> right` | `__rshift__(self, other)` | `__rrshift__(self, other)` |
| `+value` | `__pos__(self)` | 无 |
| `~value` | `__invert__(self)` | 无 |
| `-value` | `__neg__(self)` | 无 |
| `abs(x)` | `__abs__(self)` | 无 |

协议查找使用实例所属名义 Class 的原始 C3 成员，忽略动态实例字段里的同名函数。随后按现有描述符规则绑定并进入统一调用入口；协议方法自身可以使用 property、staticmethod、classmethod 或用户描述符。已经找到但不可调用的方法，以及绑定或调用时抛出的异常，都会报错，不能当作缺失方法跳过。

普通数值行为、严格 Number 检查、String／List／Tuple 的同类型 `+` 拼接保持原规则。Bool 不属于 Number。数值结果须有限，整数受 14,000 bit 上限约束；pow 在构造巨大整数前检查预算。浮点除法、取模和负数向下整除遵守已有数值实现，`floordiv(-7, 2)` 得到 -4。`**` 与 pow 使用同一数值协议；`//` 仍是注释起始符，pow 只有两个参数。位运算与增强赋值的顺序、原位协议和资源规则见[增强赋值指南](AUGMENTED_ASSIGNMENT.md)。

## 双向分派与 NotImplemented

算术候选顺序如下：

1. 若右操作数类型是左操作数类型的严格子类，且其反向方法的原始成员不同于左类对应成员，先尝试右侧反向方法。
2. 尝试左侧正向方法。
3. 名义类型不同且右侧反向方法尚未尝试时，再尝试它。

同一候选最多执行一次。两个操作数的名义类型相同，只调用左侧正向算术方法，不再尝试右侧反向方法。缺失方法或返回真正的 NotImplemented 才进入下一个候选；所有候选都拒绝时产生语言错误。

```chaos example=operators-priority
var calls = [];
class Base {
  fn __add__(self, other) { calls=append(calls,"forward"); 1 }
  fn __radd__(self, other) { calls=append(calls,"base-reverse"); 2 }
}
class Child(Base) {
  fn __radd__(self, other) { calls=append(calls,"child-reverse"); 42 }
}
class Inherited(Base) {}
let a=Base()+Child();
let b=Child()+Child();
let c=Base()+Inherited();
[a,b,c,calls]
```

结果为 `[42,1,1,["child-reverse","forward","forward"]]`。仅继承同一个反向成员不会抢先于基类正向方法。

NotImplemented 是不可伪造的内部单例，通过内建词法常量读取，`type_of` 返回 `"NotImplemented"`，可使用同名契约。它不是异常类型，也不是 false。局部名称遵守通常的词法绑定规则；同名用户值不会获得哨兵身份。

```chaos example=operators-decline
class Left { fn __add__(self, other) { NotImplemented } }
class Right { fn __radd__(self, other) { 42 } }
class Marker { fn __add__(self, other) { {"$notImplemented":true} } }
[Left()+Right(), Marker()+Right(), type_of(NotImplemented)]
```

结果为 `[42,{"$notImplemented":true},"NotImplemented"]`。只有单例身份触发回退，外观相同的 Record 就是正常结果。传输、JSON 或调试把单例显示为惰性的 `{"$notImplemented":true}` 标记；读回 JSON 不会恢复其身份。不要把这个标记当作可恢复的运行期快照。

## 比较、相等与模式匹配

比较结果只接受 Bool 或表示拒绝的 NotImplemented，不把数字、Record 或 Task 自动转换为真值。

| 操作 | 正向方法 | 交换操作数后的方法 |
| --- | --- | --- |
| `<` / `>` | `__lt__` / `__gt__` | `__gt__` / `__lt__` |
| `<=` / `>=` | `__le__` / `__ge__` | `__ge__` / `__le__` |
| `==` | `__eq__` | `__eq__` |
| `!=` | `__ne__` | `__ne__` |

右侧为严格子类时，比较先尝试右侧配对方法；这里不要求它重新定义了该方法。否则先尝试左侧。没有方法接受排序比较时抛错；实例相等先尝试用户 `__eq__`，最后才退到身份。`!=` 先尝试双方显式 `__ne__`，全部缺失或拒绝时才对运行期相等结果取反；如果定义了自己的 `__ne__`，不保证它一定等于 `! (left == right)`。

```chaos example=operators-comparison
class Score {
  var value: Int = 0;
  fn __init__(self, value: Int) { self.value=value; null }
  fn __eq__(self, other) {
    if isinstance(other, Score) { self.value==other.value }
    else { NotImplemented }
  }
  fn __lt__(self, other: Score) { self.value<other.value }
}
[Score(40)==Score(40), Score(40)!=Score(42), Score(40)<Score(42),
 Score(42)>Score(40), 1==true, 1==1.0]
```

结果为 `[true,true,true,true,false,false]`。原始值保持类型严格的相等规则，不因新增对象协议而让 Int 与 Number 浮点值或 Bool 相等。

List／Tuple／Record 的 `==` 会递归执行对象相等协议，按左侧元素顺序或字段插入顺序遍历并短路。共享预算和已访问容器对限制重复工作；调用用户协议前仍检查归属。List／Tuple 的 contains 同样按顺序用运行期相等查找；String 子串和 Record 键存在性保持原规则。

```chaos example=operators-equality-order
var seen = [];
class Item {
  var value = 0;
  fn __init__(self, value) { self.value=value; null }
  fn __eq__(self, other: Item) {
    seen=append(seen,self.value); self.value==other.value
  }
}
let equal=[Item(1),Item(2)]==[Item(1),Item(9)];
let found=contains([Item(1),Item(2)],Item(2));
[equal,found,seen]
```

结果为 `[false,true,[1,2,1,2]]`。比较可能产生 Agent／tool 效果，不能再把对象容器的相等视为无副作用的检查。

模式匹配仍使用纯结构／身份检查，不调用对象相等协议，也不执行模型。模式 guard 是普通语言表达式；若 guard 显式写 `a == b`，这次表达式仍可调用协议，最终必须返回 Bool。

## 显式 Bool、长度与包含

| 函数 | 对实例的行为 | 结果要求 |
| --- | --- | --- |
| `bool(value)` | `__bool__`；缺失时使用 `__len__`，都缺失则报错 | Bool；长度回退按是否为 0 得到 Bool |
| `length(x)` | `__len__` | 精确非负 Int，受运行期整数预算约束 |
| `contains(container, item)` | `__contains__(self, item)` | 精确 Bool，NotImplemented 不表示可用结果 |

显式 bool 对 Bool 原样返回，对 Tuple 按是否为空返回 Bool；Number、String、List、Record 等不获得隐式真值语义。if、while、match guard、`!`、`&&`、`||` 始终要求 Bool，不会主动调用实例的 `__bool__`。需要协议时写 `if bool(instance) { ... }`。长度不能是 Bool、负数或 Task。

```chaos example=operators-predicates
class Basket {
  fn __len__(self) { 2 }
  fn __contains__(self, item) { item==42 }
}
class Enabled { fn __bool__(self) { true } }
let basket=Basket();
[length(basket),bool(basket),contains(basket,42),contains(basket,7),
 if bool(Enabled()) { "enabled" } else { "disabled" }]
```

结果为 `[2,true,true,false,"enabled"]`。不会自动回退到迭代器或索引协议。min、max、sort、sum、filter、any、all 的既有语义不因本轮运算协议改变，具体约束见[标准库](STDLIB.md)。

## 宏只生成运行期协议

```chaos example=operators-macro
macro adder(value, decline) {
  quote {
    class Generated {
      fn __add__(self, other: Int) { unquote(value)+other }
      fn __radd__(self, other) -> NotImplemented { unquote(decline) }
    }
    Generated
  }
}
let other=40;
let Generated=adder!(other,NotImplemented);
[Generated()+2,other,type_of(Generated().__radd__(null))]
```

结果为 `[42,40,"NotImplemented"]`。`__add__`／`__radd__` 保持公开协议标签，self、other 和类词法名卫生重命名，unquote 保留调用者的词法身份。NotImplemented 参数传入的是 name Syntax，单例只在运行期查找；它没有成为原始 IR 的活值字面量。

宏纯阶段继续只暴露 syntax_literal、syntax_kind、syntax_value 这三个语法构造器，保持既有纯算术、容器和函数子集。它不获得运行期 bool、length、contains、pow、floordiv、abs 或对象协议。直接在纯阶段读取 NotImplemented、把 Syntax 交给 syntax_literal，或试图插入内部单例都会被拒绝；普通标记 Record 可以生成，但不因此变成哨兵。完整阶段规则见[宏规范](MACROS.md)。

## Agent 效果与异步结果

算术方法可以返回任意语言值，包括冷 Task 或生成器；不会为了完成 `+` 自动等待。比较、bool、length、contains 则必须直接得到上面的具体结果；返回 Task 会违反契约。普通方法在已有 Task 内调用 Agent 时，可以通过同一调度器挂起等待效果，不会产生新的权限或预算。

下面使用实际 Framework／Registry 链执行异步算术及普通比较，提供方是确定性的本地回调，无需外部模型服务：

```python
from chaos import Final, Framework, Registry
from chaos.settings import Settings

registry = Registry()
seen = []

@registry.provider("app.operator")
def calculate(request, context):
    context.checkpoint()
    data = request.input
    seen.append(data["op"])
    if data["op"] == "add":
        return Final(data["left"] + data["right"])
    return Final(data["left"] == data["right"])

source = '''
agent worker {provider: "app.operator", tools: [], maxSteps: 1}
class Remote {
  var value: Int = 40;
  async fn __add__(self, other: Int) {
    worker({op: "add", left: self.value, right: other})
  }
  fn __eq__(self, other: Int) {
    worker({op: "equal", left: self.value, right: other})
  }
}
let remote=Remote();
async fn exercise() {
  let tasks=[remote+1,remote+2];
  [await gather(tasks),remote==40]
}
await exercise()
'''
with Framework(registry=registry, settings=Settings(":memory:")) as app:
    project = app.create_project("operator-agent", source=source, capabilities=[])
    run = app.start(project["id"], project["version"], None, mode="source")
    assert run.result(timeout=5) == [[41, 42], True]
    events = app.events(project["id"], run_id=run.id, limit=300)["items"]
    assert sum(event["type"] == "agent.completed" for event in events) == 3
assert sorted(seen) == ["add", "add", "equal"]
```

Agent 接收显式构造的普通 Record，不接收活实例或协议闭包。Framework／Registry、API 和异步 worker 的 JSON 边界、低层受信任回调的旧 ABI，以及取消时排空在途效果的规则保持不变，见[对象边界](OBJECTS.md)和[效果生命周期](EFFECTS.md)。调试摘要、序列化、类型检查与静态分析不为了预览而调用运算协议。

## 与 Python 的区别和当前边界

反向算术优先级与比较配对参考 [Python 官方数据模型](https://docs.python.org/3/reference/datamodel.html#emulating-numeric-types)，哨兵用途参考 [NotImplemented 文档](https://docs.python.org/3/library/constants.html#NotImplemented)。Chaos 明确要求比较和成员包含返回 Bool；Python 的富比较允许其它值。Chaos 条件不进行 Python 式真值转换，bool 也不自动转换普通数字／容器。上述 `!=` 回退顺序以本页规则为准。

已支持[原位与位运算协议](AUGMENTED_ASSIGNMENT.md)，尚未加入矩阵乘、三参数 pow、用户哈希或全部元类运算协议；原生元类创建和 Class 调用见[元类规范](METACLASSES.md)，不承诺 Python 运算生态完整兼容。用户同步与异步迭代另见[迭代器指南](ITERATORS.md)。类、描述符和运算仍受本系统资源上限约束；显式 Agent 效果不因表达式写法而成为纯计算。

条目操作独立于算术、比较及 contains 协议，见[索引与切片](INDEXING.md)。contains 与 iter 不会因为声明了 __getitem__ 而自动退回逐项调用。

## 原生 Set 运算

两个原生 Set 的普通比较、查找与代数沿共享键协议执行；纯键不调用用户代码，用户 Instance 键可同步执行 __hash__／__eq__。`| & - ^` 返回新集合，`< <= > >=` 表示子集／超集关系。Set 与外部用户 Instance 的二元运算仍走既有运算分派。纯模式匹配中的协议集合仅按身份比较。规则见[集合](SETS.md)。

## 运算协议的执行状态

协议调用、比较候选、结构相等、二元分派、原位回退和原生运算入口的六段完整挂起算法由同一求值游标上的类型化帧执行。帧保存实际接收者、方法、参数、候选位置、相等遍历工作表、待决结果与阶段；这些字段驱动继续执行，不是另外生成的调试描述。描述符绑定与普通调用仍使用原调用合同。

原位方法先执行；只有缺失或返回真正的 NotImplemented 才进入二元分派，子类反向方法的优先级保持不变。错误不触发回退，已完成的回调和工具效果不会重放。相等比较保留左侧顺序、循环保护及含用户实例子树不跨出现次数缓存的规则。显式 async 算术方法仍返回冷 Task；比较、长度、成员查找和 bool 仍要求原来的具体结果类型。

```chaos
var calls=[];
class Left {
 fn __iadd__(self,other){calls=append(calls,"inplace");NotImplemented}
 fn __add__(self,other){calls=append(calls,"add");42}
 fn __eq__(self,other){calls=append(calls,"equal");other==42}
}
class Right(Left){fn __radd__(self,other){calls=append(calls,"reverse");NotImplemented}}
var answer=Left();answer+=Right();
[answer,[Left()]==[42],calls]
```

结果为 `[42,true,["inplace","reverse","add","equal"]]`。源码、两种 IR 方言、Code／Program、宏、AgentTool、Team、Bot 与 CLI／SDK／HTTP 共用这些状态。

Set／HashMap 的内部比较、查找和代数仍沿各自已有算法边界执行；本次迁移不宣称这些内部算法已经类型化，也没有新增保存或复用 continuation 的接口。原权限、所有权、图预算、检查点、取消和强制关闭规则保留。执行状态及剩余边界见[默认执行帧](EXECUTION_FRAMES.md)。
