# Chaos 语言参考

本页描述当前语言计算核心；新源码默认采用 **源码方言 2**，并兼容明确标注的方言 1。`chaos.program/1` 是结构 IR 的模式标识，不是当前源码方言。软件包版本由 `chaos/version.py` 管理，与语言核心版本分开演进；已安装版本可用 `python -c 'import chaos; print(chaos.__version__)'` 查看。安装和首次运行见[快速开始](GETTING_STARTED.md)，所有内建函数见[标准库](STDLIB.md)，组合、状态与模块设计见[进阶编程](ADVANCED.md)。

Chaos 是严格求值、词法作用域、函数为一等值的动态语言。解释器运行 `.chaos` 源码，不使用 Python `eval`，也不依赖网页或模型完成普通计算。工具、智能体与工作流是显式效果；纯计算和效果调用使用同一套表达式语法。

通用键状态使用[HashMap 与用户哈希协议](HASH_MAPS.md)；Record 继续只使用字符串键，原生 Set 同样接入用户哈希协议，纯宏／宿主模式保留无钩子边界。

函数还支持按实际类型扩展与组合实现，见[泛型函数与方法组合](GENERIC_FUNCTIONS.md)。泛型值沿用 Function 和普通调用语法，不增加关键词。

## 1. 第一个程序与执行结果

```chaos example=lang-first
let numbers = [1, 2, 3, 4];
fn square(x: Int) -> Int { x * x }
map(square, numbers)
```

结果是 `[1, 4, 9, 16]`。模块或块中最后一条语句的值就是结果；空块返回 `null`。把代码保存为 `main.chaos` 后运行 `chaos run main.chaos`，命令行向标准输出写入 JSON 结果。

Python 中可直接做无外部效果的计算：

```python
from chaos import Framework

result = Framework.evaluate("map(fn(x) { x * x }, input)", {"input": [1, 2, 3]})
assert result == [1, 4, 9]
```

`Framework.evaluate(source, bindings=None, *, step_limit=30000, timeout=20, files=None, entry="main.chaos", trace_handler=None)` 返回可序列化的结果，不创建已存储运行。它不允许调用工具、智能体或工作流效果。`input` 是宿主提供的绑定：CLI `--input-json` 默认传入 `null`；直接使用 `Framework.evaluate` 时须显式提供所需绑定。

## 2. 词法、字面量与分隔符

源码为 UTF-8。标识符支持 Unicode 字母和下划线开头，后续可含数字，例如 `total`、`用户数`。名称区分大小写。`//` 和 `#` 开始行注释；没有块注释。

```chaos example=lang-literals
// 十进制整数、小数、科学计数法、两种字符串引号
let 用户数 = 3;
[用户数, 1.5, 2e2, "hello\nChaos", '你好', true, false, null]
```

结果为 `[3, 1.5, 200.0, "hello\nChaos", "你好", true, false, null]`。整数与浮点数保留类型区别；数值必须有限。没有十六进制字面量、字符串插值或模板字符串。双引号字符串采用 JSON 转义；常用的 `\n`、`\t`、`\\` 与转义引号适用于字符串书写。

分号可以分隔语句，普通表达式中的空白和换行本身不结束表达式。装饰器行是明确例外：@ 后在括号外的换行结束该装饰器表达式。为避免相邻表达式被解析为调用，建议每条绑定或赋值后写分号。`return` 后换行不代表返回 `null`，需要明确写 `return null`、`return;`，或在紧邻 `}` 前写 `return`。

保留字：`let fn agent if else true false null rec data match try catch throw do import as export var while for in break continue return`。`finally` 仅在 `try` 后作为清理分支关键字识别，不是全局保留字，已有 `finally` 变量或函数名仍可使用。

## 3. 值与类型契约

| 契约名 | 接受的值 | 示例 |
| --- | --- | --- |
| `Any` | 任意语言值，省略注解时的默认契约 | `let x = 1` |
| `Int` | 整数，排除 Bool | `42` |
| `Number` | Int 或有限浮点数，排除 Bool | `42`、`1.5` |
| `String` | Unicode 字符串 | `"中🙂"` |
| `Bool` | 布尔值 | `true`、`false` |
| `Null` | 空值 | `null` |
| `NotImplemented` | 仅内部拒绝单例 | `NotImplemented` |
| `List` | 列表，不限制元素类型 | `[1, "a"]` |
| `Set` | 持久集合，纯键或同步用户哈希键，按首次插入顺序迭代 | `{20,22}`、`{*[]}` |
| `Tuple` | 不可变有序容器，不限制元素类型 | `(1, "a")`、`(42,)` |
| `Record` | 字符串键记录 | `{name: "Ada"}` |
| `HashMap` | 原生持久有序通用键映射 | `hash_map([((1,2),42)])` |
| `Contract` | 组合／递归契约描述值本身（含开放模板） | `contract_list("Int")` |
| `Type` | 原生 Type 或其子类，表示元类 | `Type` |
| `Class` | 本解释器中的类值 | `let T:Class = Counter` |
| `Instance` | 本解释器中的实例值 | `let c:Instance = Counter()` |
| `Descriptor` | 内置描述符或类型定义了用户访问协议的实例能力契约 | `staticmethod(fn(x){x})` |
| `Function` | 用户函数、内建函数、智能体、类、绑定方法、静态方法包装或可调用实例 | `fn(x) { x }` |
| `Task` | 当前运行的异步任务句柄 | `async fn() { 42 }()` |
| `Generator` | 当前运行的可恢复生成器句柄 | `fn*() { yield 42 }()` |
| `AsyncGenerator` | 当前运行的可挂起异步流句柄 | `async fn*() { yield 42 }()` |
| `Iterator` | 当前执行周期中的同步迭代适配句柄 | `iter([40,42])` |
| `AsyncIterator` | 当前执行周期中的用户异步迭代适配句柄 | `aiter(source)` |
| 自定义类型 / `module.Type` | 对应 data 类型、对应名义类及子类实例，或已绑定组合契约描述的数据 | `Some(1)` |

`Agent` 不是内置契约名；智能体沿用 Function 调用合同。`Type` 是实际存在的原生元类，其契约接受元类值；虽然 `type_of` 对 DataType 描述值也返回历史名称 `"Type"`，这不使 DataType 成为元类。结构化与联合检查使用[组合契约](CONTRACTS.md)的普通构造函数和命名别名；递归数据以 contract_ref／contract_recursive 的闭合组声明，开放引用不能作为实际数据注解；没有 `List[Int]` 等内联泛型语法或完整静态类型系统。编辑器可以从字面量、已知函数签名和局部结构保守推断提示类型，例如 `let value = 42` 显示 Int；未注解的 `var value = 42` 仍显示 Any，因为后续可以写入其他类型。这些提示不改变运行时契约，也不会执行用户代码。

```chaos example=lang-contracts
var count: Int = 1;
let message = try { count = "two" } catch error { error.message };
[count, type_of(count), type_of(number("2")), length(message) > 0]
```

结果是 `[1, "Int", "Number", true]`。绑定初值、`var` 更新、函数实参和返回值均在运行时检查契约；失败的赋值不更新原值。`data`、名义类和组合契约在函数创建时捕获对应实际描述值。

## 4. 列表、元组、记录与索引

```chaos example=lang-containers
let xs = [10, 20, 30];
let person = {name: "Ada", "home-city": "London"};
let changed = set(person, "name", "Lin");
[xs[0], person.name, person["home-city"], changed.name, person.name, slice(xs, -2)]
```

结果是 `[10, "Ada", "London", "Lin", "Ada", [20, 30]]`。原生 Record 的 `value.field` 与 `value["field"]` 都读对应键；自定义对象的点访问是属性，方括号优先使用条目协议。当前语言版本的列表、元组和字符串下标接受 `Int`，负数从末尾计数；越界和缺字段会报错。旧版本与显式 `value.[key]` 保留原来的访问语义，见[索引兼容规则](INDEXING.md)。`get(record, key, default)` 可以提供默认值。记录字面量不能包含重复键。

原生 List、Tuple、Record 和 String 使用值式更新，不支持对它们执行 `xs[0] = ...` 或 `record.field = ...`。使用 `set(record, key, value)`、`append(list, value)`，需要保留更新结果时赋回 `var`。类实例另有可写成员和用户条目协议，例如声明了 `var value` 的实例可以执行 `instance.value = 42`；具体约束见[对象成员](OBJECTS.md)与[索引协议](INDEXING.md)。字符串索引、长度与迭代以 Unicode 码点计数，不按可见字形簇计数。

## 5. 运算符与求值顺序

从低到高的优先级如下；除 `**` 右结合外，同级二元运算符左结合，可用括号明确分组。表内数值与集合行为描述原始值语义；Instance 还可使用第 18 节的用户运算协议。

| 优先级 | 运算符 | 行为 |
| --- | --- | --- |
| 1 | `\|>` | 把左值作为一个参数传给右侧可调用值 |
| 2 | `\|\|` | Bool 短路或 |
| 3 | `&&` | Bool 短路与 |
| 4 | `== !=` | primitive 类型严格相等；容器递归比较，实例可定义协议 |
| 5 | `< <= > >=` | 数值比较 |
| 6 | `\|` | Int 按位或 |
| 7 | `^` | Int 按位异或 |
| 8 | `&` | Int 按位与 |
| 9 | `<< >>` | Int 左移、右移 |
| 10 | `+ -` | 数值加减；同类型 List / String 可用 `+` 连接 |
| 11 | `* / %` | 数值乘除、余数 |
| 12 | `! + - ~` | Bool 非、数值正负、Int 按位取反 |
| 13 | `**` | 幂，右结合；指数可带一元正负号 |
| 14 | `await` | 显式等待 Task |
| 15 | `f(...) x[i] x.field` | 调用与索引 |

`=` 是绑定或赋值语句中的符号，不是上表中的二元表达式运算符。用 `name = expression` 更新最近的可写绑定，用结构模式批量更新已有变量，用成员／条目赋值更新支持该协议的对象；支持 `+= -= *= /= %= **= &= |= ^= <<= >>=`，目标只求值一次；不支持 `a = b = value` 连锁赋值。详见[增强赋值](AUGMENTED_ASSIGNMENT.md)。编辑器仍将 `=` 按运算符类别着色，颜色分类不代表它可以出现在任意表达式位置。

```chaos example=lang-operators
let doubled = 3 |> fn(x) { x * 2 };
[1 == 1.0, true == 1, [1, 2] == [1, 2], 7 / 2, 7 % 2,
 false && (1 / 0 > 0), doubled, "a" + "b", [1] + [2]]
```

结果是 `[false, false, true, 3.5, 1, false, 6, "ab", [1, 2]]`。比较运算不支持字符串排序，排序列表请用 `sort`。布尔值不能隐式当数值；条件不使用 JavaScript/Python 式真假值转换。普通实参和运算参数从左到右求值，只有 `&&`、`||` 短路。

`x |> f` 等价于 `f(x)`；`x |> f(a)` 的右侧先求出 `f(a)`，并不会自动变成 `f(x, a)`。多参数组合请使用 `partial` 或匿名函数。函数值按身份相等，列表和记录从左到右递归比较；用户实例可以通过比较协议定义相等性。模式匹配仍使用无效果的结构/身份检查，不调用运算协议。

## 6. 绑定、作用域与函数

```chaos example=lang-functions
let offset = 10;
fn add(x: Int, y: Int) -> Int { x + y }
let addOffset = fn(x: Int) -> Int { add(x, offset) };
do { let offset = 100; addOffset(2) }
```

结果是 `12`。`let name [: Type] = expression` 不能重新赋值；`var name [: Type] = expression` 允许通过 `name = expression` 更新。同一作用域不可重复声明，内层可以遮蔽外层。赋值只查找最近同名绑定；遇到 `let` 时即失败，不能越过它更新更外层的 `var`。

`fn name(parameters) [-> Type] { ... }` 声明命名函数，`fn(parameters) [-> Type] { ... }` 创建匿名函数。两种形式支持位置专用参数、位置或具名参数、默认值、仅关键字参数及剩余参数。

### 参数模式与默认值

例如 `fn consume(value, /, **options) { [value, options] }` 的 `consume(20, value=22)` 得到 `[20,{value:22}]`：关键字保留在 options 中，不会替代位置参数。没有 `**options` 时多余关键字报错；必需的位置参数仍不能省略。完整示例及私有名称重构见[调用与组合](FUNCTION_COMPOSITION.md#位置专用参数)。

| 声明 | 绑定规则 |
| --- | --- |
| `a` / `a: Int` | 必需参数，可以按位置或名称传入 |
| `a as pattern: Type` | 保留参数 `a` 的调用标签，同时在函数体前完整解构；类型可省略 |
| `b: Int = 2` | 未传入时使用定义时已算好的默认值 |
| `*rest: List` | 收集多余位置参数；后续普通参数只能按名称传入 |
| 裸 `*` | 不收集位置参数，只把后续参数改为仅关键字参数 |
| `required` / `option = 3`（在 `*` 后） | 必需 / 可省略的仅关键字参数 |
| `**options: Record` | 收集未匹配的具名参数，必须位于声明末尾 |

`/` 前是位置专用参数，不能通过同名关键字填入；`/` 后、`*` 前的参数仍可按位置或名称传入。`/` 只能出现一次，之前至少有一个参数，且必须位于任何 `*`／`**` 之前。必需位置参数不能出现在已有默认值的位置参数之后，此限制跨越 `/`；仅关键字参数不受这个先后限制。每种剩余参数最多一个；剩余参数不能再声明默认值。裸 `*` 后至少要有一个普通的仅关键字参数，不能只跟 `**options`。`*rest` 接收一个完整 List、`**options` 接收一个完整 Record，注解检查整个容器，不等同于约束其中每个元素。需要逐项约束时可将 contract_list 或 contract_record 的结果绑定为命名契约；仍检查整个剩余参数容器。

```chaos example=lang-parameter-modes
fn collect(first: Int, scale: Int = 2, *rest: List,
           required: String, option: Int = 3, **options: Record) {
  [first, scale, rest, required, option, options]
}
fn greet(name, *, prefix = "Hi") { prefix + " " + name }
[collect(1, 2, 3, 4, required="batch", extra=true),
 greet("Chaos", prefix="Hello")]
```

结果是 `[[1, 2, [3, 4], "batch", 3, {"extra": true}], "Hello Chaos"]`。`collect(1)` 缺少必需的 `required`；`greet("Chaos", "Hello")` 则不能用第二个位置值填充仅关键字的 `prefix`。

默认表达式在函数定义时，按声明从左到右、在外层词法环境中求值一次，并立即检查契约。以后调用省略参数时复用默认值，不重新执行默认表达式；显式传参也不会阻止定义时求值。默认表达式不在调用帧中运行，不能把前一个参数当作本次实参来引用。它不能用 `return/break/continue` 跳出外围函数或循环；默认表达式自己定义的函数或循环仍按各自的控制流规则执行。

```chaos example=lang-defaults-once
var defaults = 0;
fn nextDefault() { defaults = defaults + 1; defaults }
fn configured(a: Int = nextDefault(), b: Int = nextDefault()) { [a, b] }
[defaults, configured(), configured(a=9), defaults]
```

结果是 `[2, [1, 2], [9, 2], 2]`。需要每次调用重新计算的值时，将计算放到函数体中，而不要放到默认表达式里。

### 调用、展开与绑定错误

调用支持 `f(1, b=2)`、`f(*values)`、`f(**options)` 及组合形式。`*expression` 按同步 iter 协议展开 List、String、Generator、Iterator 或用户可迭代对象，`**expression` 只能展开 Record；位置值和 `*` 展开都必须放在具名参数及 `**` 展开之前。没有 Python 式允许后置 `*values` 的混合顺序。

```chaos example=lang-call-spreads
var order = [];
fn note(label, value) { order = append(order, label); value }
fn calculate(a, b, *, scale = 1) { (a + b) * scale }
let result = calculate(note("a", 2), *note("rest", [3]),
                       **note("options", {scale: 4}));
[result, order]
```

结果是 `[20, ["a", "rest", "options"]]`。函数表达式先求值，实参及展开表达式按源码顺序求值。具名参数标签不读取同名变量；例如 `b=2` 指定参数 `b`。参数名作为标签时允许 `fn` 等保留字，以便调用具有这些真实参数名的内建函数。

位置值和具名值重复绑定同一可具名参数、重复具名字段、缺少必需参数、额外位置值或未知名称都会报错，且不会进入函数体。同一调用重复写显式标签（如 `f(a=1, a=2)`）是语法错误；由 `**` 展开产生的字段碰撞属于运行时 `argument_error`。参数表达式已经发生的效果不会因此撤销。调用没有隐式柯里化：省略必需参数会失败，部分应用请显式使用 `partial`；同步可迭代位置参数包和记录具名参数包可用 `apply`，见[标准库](STDLIB.md)。

函数捕获创建时已经存在的词法绑定：`let` 的值快照、`var` 的共享单元。创建函数之后才声明的名字不会补进旧闭包。命名函数可递归调用自身；互递归必须使用 `rec` 函数组。`rec` 只能容纳命名函数，且整组名称冲突检查具有原子性。

```chaos example=lang-recursion
rec {
  fn even(n: Int) -> Bool { if n == 0 { true } else { odd(n - 1) } }
  fn odd(n: Int) -> Bool { if n == 0 { false } else { even(n - 1) } }
}
[even(20), odd(20)]
```

结果是 `[true, false]`。函数末表达式、`if/match/do` 尾分支、显式 `return` 和逻辑右侧支持尾调用，返回契约沿整条尾调用链检查。普通非尾调用有深度上限；详情见[进阶递归](ADVANCED.md#递归与有限执行预算)。

## 7. 条件、块、循环与返回

`if condition { yes } else { no }` 是表达式，必须写 `else`；可继续接 `else if`。条件必须是 `Bool`。`do { ... }` 建立局部块，用于嵌入需要多条语句的计算。

```chaos example=lang-control
var total = 0;
for n in [1, 2, 3, 4, 5] {
  if n == 2 { continue } else { null };
  if n > 4 { break } else { null };
  total = total + n;
}
fn classify(n) {
  if n < 0 { return "negative" } else { null };
  if n == 0 { "zero" } else { "positive" }
}
[total, classify(total), do { let x = 2; x + 1 }]
```

结果是 `[8, "positive", 3]`。`while condition { ... }` 每轮重算条件；`for item in iterable { ... }` 仅计算一次来源，再依照同步迭代协议按顺序消费 List、Tuple、Set、String、Generator、Iterator 或定义了 `__iter__` 的对象。每轮 `for` 创建独立的只读 `item` 绑定，因此循环中产生的闭包保留各自那一轮的值。未带 else 的循环正常完成或 `break` 后均返回 `null`。可加 `else { ... }`，在条件为 false 或来源耗尽时执行，并将该块的值作为循环结果；break 跳过 else。`async for` 与结构目标遵循同一规则，详见[正常完成分支](CONTROL_COMPLETION.md)。

`break/continue` 仅作用于当前函数的最近循环，不能跨函数边界控制调用者。`return expression` 仅允许在函数内，可从分支、循环或 `try` 提前返回。控制信号不会被 `catch` 当成业务错误捕获。

## 8. data 与模式匹配

```chaos example=lang-data
// data 只能声明在模块顶层；无字段构造器也要写 () 调用。
data Response { Missing, Found(value) }
fn describe(response: Response) -> String {
  match response {
    Found(x) if x > 0 => "positive",
    Found(_) => "other",
    Missing() => "missing"
  }
}
[describe(Found(4)), describe(Missing())]
```

结果是 `["positive", "missing"]`。声明产生类型描述值 `Response` 和构造器 `Missing`、`Found`。构造器支持按字段名称调用，例如 `Found(value=4)`；位置和具名参数遵守相同的重复绑定检查。类型名不能覆盖内置类型，构造器字段只声明名称和数量，没有独立字段类型契约。

`match value { pattern [if guard] => expression, ... }` 对 `value` 求值一次，从左到右选择第一个模式和守卫均通过的分支。守卫必须为 Bool，未匹配时抛出 `match_error`。分支需要多条语句时用 `do { ... }`。支持以下模式：

| 模式 | 匹配规则 |
| --- | --- |
| `_` / `name` | 忽略 / 绑定任意值 |
| `42`、`-1`、`"x"`、`true`、`null` | 类型严格的标量匹配 |
| `Found(x)` / `module.Some(x)` | 按当前词法绑定的构造器匹配 |
| `[a, b]` / `[head, ..tail]` | 完整列表 / 前缀及剩余列表 |
| `{x: value}` / `{x: value, ..rest}` | 完整字段集合 / 指定字段及剩余记录 |

```chaos example=lang-patterns
match {name: "Ada", scores: [8, 9], active: true} {
  {name: who, scores: [first, ..others], ..rest} =>
    [who, first, others, rest]
}
```

结果是 `["Ada", 8, [9], {"active": true}]`。没有 `..rest` 的容器模式要求完整形状；剩余项可以写 `.._` 忽略。一个模式内不能重复绑定相同名字，失败分支不泄漏变量。

data 值的运行协议是 `{"$type":"规范模块名::类型名","$tag":"构造器","$values":[字段值...]}`。普通记录也能构造同形协议；契约验证类型身份、标签、字段数量，不提供不可伪造的宿主封装。模式中的构造器按绑定解析，不能只凭标签文字跨类型匹配。

## 9. 错误与恢复

```chaos example=lang-errors
let domain = try { throw {reason: "invalid input"} } catch error {
  [error.code, error.value.reason]
};
let arithmetic = try { 1 / 0 } catch error { error.code };
[domain, arithmetic]
```

结果是 `[["user_throw", "invalid input"], "ZeroDivisionError"]`。未带 else 的 `try { ... } catch error { ... }` 的结果是成功块或处理器的结果。添加 `else { ... }` 后，仅受保护块正常完成才执行 else，成功值来自 else；handler 成功不会触发它。else 的错误绕过同级 catch，可继续加 finally；详见[正常完成分支](CONTROL_COMPLETION.md)。`error` 是含 `code`、`message`、`value`、`location` 的记录；`location` 含 `file`、`line`、`column`，没有位置时可以为 `null`。

类型、索引、算术、匹配错误可以处理。`throw value` 抛 `user_throw`；`value` 可为 `false`、`0` 或 `null`，不会因是假值而丢失。处理器里再次抛出的错误由外层处理器捕获。语法和模块链接在运行前失败，源码中的 `try` 无法包住这一步。

资源限制和取消属于运行控制，不能被普通 `catch` 吞掉。工具及提供方普通异常分别归一化为 `tool_error`、`provider_error`；宿主堆栈不会成为语言值。避免依赖错误消息的精确文本；分支处理优先使用 `code`。

0.55 支持多个 `catch pattern if guard { ... }` 分支和裸 `throw` 动态重抛。模式匹配完整错误记录，守卫严格 Bool；不匹配继续下一个分支，匹配／守卫／处理器的新错误直接向外传播。独立 Task/生成器隔离活动错误，同步函数和尾调用继承当前上下文。单个无守卫名字 catch 保持历史节点，详见[模式异常与重抛](EXCEPTIONS.md)。

### finally：正常退出和语言控制流的清理

在 `try` 后添加 `finally { ... }`，可以省略 `catch`，也可以保留 `catch error { ... }`。已有 `try/catch` 语法不变。`try` 至少需要 `catch` 或 `finally` 之一。

```chaos example=lang-finally-return
var events = [];
fn compute() { events = append(events, "value"); 42 }
fn work() {
  try { return compute() }
  finally { events = append(events, "cleanup") }
}
let result = work();
[result, events]
```

结果是 `[42, ["value", "cleanup"]]`。保护块（包括已有 catch 和可选 else）正常完成、抛出可处理的语言错误或发出 `return/break/continue` 时，都会先执行清理块，再继续原来的结果或控制流。尾位置 `return compute()` 的调用先完成，再清理；跨 `finally` 动态范围的递归不保证常量调用栈。

```chaos example=lang-finally-catch
var events = [];
let result = try { throw "bad" }
catch error { events = append(events, error.value); "recovered" }
finally { events = append(events, "cleanup") };
[result, events]
```

结果是 `["recovered", ["bad", "cleanup"]]`。`catch` 的结果或再次抛出的错误也经过清理。清理块有独立词法作用域，只能看到外层绑定，不能访问保护块内的局部绑定或 `catch` 的错误变量；需要共享的状态应提前在外层声明。

清理块正常完成时，它的最终值被丢弃，保留原待决结果。清理块显式 `return` 或 `throw` 可以替换普通待决返回值、错误或控制流，因此应谨慎使用。嵌套清理从内到外执行。

**资源限制与已交付的取消不执行清理块。** 显式[取消保护](CANCELLATION.md)可在局部取消交付前完成其内部清理；运行级取消和预算仍立即生效。 `ResourceLimit` 或宿主 `ExecutionControl` 一旦发生就直接向外传播，不能通过 `finally` 获得额外预算；清理期间发生这些硬控制也始终向外传播。需要保证宿主资源释放时，由宿主工具负责其生命周期，不能依赖语言清理在超时或取消后继续运行。

## 10. 模块与封闭程序包

模块以 `.chaos` 文件组织，顶层用 `import "./module.chaos" as alias` 导入，通过 `export {name, other}` 指定导出。本地路径从导入文件所在目录解析，不是从进程工作目录解析；标准库使用 `std/list` 等内置路径。

```chaos example=lang-modules
import "./lib/math.chaos" as M;
M.double(input)
```

本例传入 `input = 21`，并提供伴随文件 `lib/math.chaos`：

```chaos
fn double(x: Int) -> Int { x * 2 }
export {double};
```

结果是 `42`。在 Python 中显式构造相同的封闭包：

```python
from chaos import Framework
from chaos.modules import ProgramBundle

source = 'import "./lib/math.chaos" as M; M.double(input)'
files = {"lib/math.chaos": "fn double(x: Int) -> Int { x * 2 }; export {double}"}
bundle = ProgramBundle(source, files=files, entry="main.chaos")
assert bundle.summary()["valid"] is True
assert Framework.evaluate(bundle, {"input": 21}) == 42
```

全部提供文件（包括未导入文件）在任何顶层效果之前完成解析、导出和依赖图检查。缺文件、重复顶层声明、非法导出、循环依赖和越界路径会使整个包失败。不能直接导出 `var`，请导出读取/更新闭包。导入、导出、data 声明仅限模块顶层。

每次完整运行按规范路径缓存模块，最多初始化一次；不同运行缓存隔离。SDK/HTTP 导入仅查 `files` 字典，不隐式读取宿主文件。CLI 的显式文件适配器只装载依赖文件，限制在 `--root` 内并拒绝符号链接越界。`source` 是入口源码的权威副本，即使 `files` 含同名入口也以 `source` 为准。

## 11. 智能体、工具与工作流效果

以下是语法示意，须在已配置对应提供方和能力的集成运行里执行：

```chaos
agent analyst {
  provider: "app.provider",
  prompt: "Summarize the supplied text.",
  tools: ["app.lookup"],
  maxSteps: 4
}
analyst(input)
```

智能体是一等可调用值，接受一个输入，可按位置调用 `analyst(value)`，也可写 `analyst(input=value)`；不能同时传两种形式。它也可传给高阶函数。`tool("name", input)` 显式调用获授权工具，`workflow(graph, input)` 显式执行工作流。声明的工具集合、父上下文能力、共享预算、取消和输入输出边界共同约束执行；提示词不会授予额外权限。配置与扩展实例如[SDK](SDK.md)所述。

函数和智能体可在同一次求值中组合；不能跨独立 Evaluator 直接传递可调用对象。跨 API、图边和持久化应传 JSON 数据或源码。`Framework.evaluate` 对返回的函数/类型生成描述记录，不生成可恢复执行的序列化闭包。

## 12. 工具链、REPL 与表示

| 命令 | 用途 |
| --- | --- |
| `chaos run main.chaos --input-json '21'` | 执行程序，输出 JSON |
| `chaos check main.chaos` | 检查语法、包依赖与导出结构 |
| `chaos fmt main.chaos` | 规范源码写到 stdout，不修改原文件 |
| `chaos ir main.chaos` | 入口文件的 `chaos.program/1` IR |
| `chaos repl [main.chaos]` | 进入持久词法会话，可预加载文件 |
| `chaos run main.chaos --trace` | stderr 输出最多 2000 条表达式位置记录 |

`--root` 设置包根目录；文件参数 `-` 从标准输入读取单入口源码。REPL 支持 `.env` 查看绑定、`.reset` 重置会话、`.quit` 退出。会话复用模块初始化与闭包状态，每次提交重置执行预算，不重放此前语句。

`check` 不执行程序，也不证明任意动态名称、类型契约或终止性。从 0.27 起，CLI `fmt`、IDE、Framework 和 Client 的 `format_source` 共用保留注释与字符串的格式化器；`fmt` 仍只写 stdout，不改文件；仅解析指定单文件，不加载导入模块或展开宏，依赖完整性请用 `check`。可选 `--line-width 40..240`、`--indent-size 1..8`、`--language-version 1|2`，默认 88 列、2 空格和方言 2。宽度是排版目标，不拆长字符串或注释。方言 1 的旧下标会打印为显式 `.[key]`，经 AST 检查保持原义。结构编辑与 `format_program(IR)` 仍从结构重建规范源码，不保留原始注释；普通源文本保存保留原文。IR 节点含 `id/kind/span`；`finally` 节点的 `body` 是受保护块或原有 `try` 节点，`cleanup` 是清理块，原 `try` 节点字段保持兼容。`loop_else` 包含 `loop/otherwise`，`try_else` 包含 `body/otherwise`，后者 body 为 try 节点；两者 otherwise 为块，finally 可包住 try_else。浮点数与超出 JavaScript 安全整数范围的 Int 使用 `{"$number":"精确文本"}` 保真编码。函数 IR 保留旧参数 `{name, type}` 形状；扩展参数使用 `{name, type, mode, default}`，`mode` 为 `positional_only / positional / keyword_only / varargs / varkw`，`default` 是表达式节点或表示无默认值的 `null`。默认值若为语言 `null`，仍需用字面量节点表示，不能与“无默认值”混淆。调用 `arguments` 保留普通表达式，并用 `keyword / spread / kwspread` 节点表达具名和展开参数。它们参与语义哈希与源码格式化往返。带 `as` 的函数参数额外包含 `pattern` 字段，值复用 `p_*` 模式节点；旧参数字段不变。

布局不进入 `semanticHash`；结构编辑保留既有节点身份并刷新位置，文本改写可能产生新身份。

源码、IR、结构投影和可还原的工作流投影表示同一个保存版本。关系画布投影顶层 `workflow({...}, input)`，任意函数与控制流使用结构编辑器。运行 `mode` 是调用来源标签，不选择隐藏的另一份程序。协作与快照约定见[实时同步](REALTIME.md)。

## 13. 实现边界

当前语言提供 [宏](MACROS.md)、[生成器与语言级 async/await](EFFECTS.md)，以及 [断点调试器](DEBUGGER.md)。0.66 已实现[原生元类及 prepare 命名空间](METACLASSES.md)。尚未提供完整静态类型推导、全局惰性求值、通用原生机器码编译器或任意 Python/Lisp 库兼容。可选的受约束标量区域 JIT 见[原生函数](NATIVE_FUNCTIONS.md)。语法与运算的完整性是对上文限定范围的承诺，不代表无限资源或任意外部效果都可用。

源码、包、值、调用深度与执行时间均有明确上限，详见[进阶预算表](ADVANCED.md#递归与有限执行预算)。抽象图灵完备性与有限实现资源是两个层面的讨论；模型响应的正确性也不由语言计算能力保证。


## 14. 双向流、异步流与模块宏

`fn*` 的 yield 接收 send 的返回值，并可处理 throw_into 注入的语言异常。`async fn*` 同时允许 await/yield；`async for` 对 AsyncGenerator 逐项等待。函数参数、返回契约、词法闭包和宏仍使用相同规则，详见[流与任务](EFFECTS.md)。

模块可以显式 export 宏，导入方用 `Alias.name!(arguments)` 调用。该形式是编译期命名空间，不求值某个运行时对象；未导出宏不可从外部调用。定义处的辅助宏与调用者通过 unquote 传入的语法保留各自解析环境。运行时 import 记录只包含运行期导出，详见[宏规范](MACROS.md)。

这些语法继续使用 `chaos.program/1`：async_gen_fn 与其它函数节点具有相同字段，async_for 与 for 相同；macrocall 的 name 可为本地名称或静态一级导入限定名。既有节点形状和旧程序的语义保持兼容。

## 15. 类、绑定方法与可调用组件

`class Name(Base1, Base2) { ... }` 声明名义类；基类列表可省略。类体由 `let`、`var` 和命名函数声明组成，命名方法也可以带装饰器；普通方法首个位置参数显式接收实例。方法可以使用普通、异步、生成器或异步生成器语法。类也可以在词法局部作用域声明，每次求值得到独立的类身份。

```chaos example=lang-class-callable
class Counter {
  var total:Int = 0;
  fn __init__(self, start:Int=0) { self.total=start; null }
  fn __call__(self, amount:Int=1) { self.total=self.total+amount; self.total }
}
let counter:Counter = Counter(40);
let add = counter.__call__;
[apply(add, [2]), isinstance(counter, Counter), class_of(counter)==Counter]
```

结果为 `[42, true, true]`。`Function` 契约也接受类构造器、绑定方法和实现调用协议的实例。`counter.total = value` / `counter["total"] = value` 更新实例成员；不会改变 List 或 Record 原有的不可变更新方式。`super(CurrentClass, self)` 沿接收者真实 C3 顺序查找后续成员。普通属性可通过实例／元类五钩子定制，default_* 沿原描述符与契约委托；隐式特殊方法和纯模式仍绕过定制，见[属性协议](ATTRIBUTE_PROTOCOL.md)。完整规则和与 Python 的区别见[对象与组件](OBJECTS.md)。

## 16. 装饰器与描述符

装饰器是运行期的普通可调用值；`@表达式` 按逻辑行写在函数、类或 Agent 声明之前。表达式自上而下求值，随后创建原始声明，再自下而上应用装饰器，最后发布替换值。错误不会发布半成品绑定，但不会回滚外部效果。宏仍在此前展开 AST。

```chaos example=language-decorator-count
fn counted(action) {
  var calls=0;
  fn(value) { calls=calls+1; {value:action(value),calls:calls} }
}
@counted
fn twice(value) { value*2 }
[twice(20),twice(21)]
```

`staticmethod` 不绑定接收者，`classmethod` 绑定实际访问类，`property` 在实例读取时调用 getter。类读取 property 得到 `Descriptor` 值；调试预览不会触发 getter。异步 getter 返回 Task，需要显式 await；setter 必须同步完成并返回 null。完整顺序、访问器更新和作用域规则见[装饰器规范](DECORATORS.md)。

## 17. 用户定义的属性协议

在类成员中存放一个定义了 `__get__`、`__set__` 或 `__delete__` 的实例，可以使普通成员访问进入用户语言方法。协议依据该值类型的 C3 MRO 查找，实例动态字段中的同名函数不改变协议。`__set_name__` 在拥有者全部成员形成后通知自身成员；继承不会重复通知。

```chaos example=language-user-descriptor
class Answer {
  fn __get__(self, object, owner) -> Int { 42 }
}
class Box { let answer = Answer() }
let box = Box();
box.answer = 40;
let cached = box.answer;
delattr(box, "answer");
[cached, box.answer]
```

结果 `[40,42]`：仅有 getter 的非数据描述符允许实例遮蔽，即使类声明为 let；删除实例遮蔽值后恢复 getter。含 setter 或 deleter 的数据描述符使用相应访问器，缺失所需方法会报错；普通 let 成员仍只读。详见[用户描述符协议](DESCRIPTOR_PROTOCOL.md)。

## 18. 用户定义的运算协议

`+ - * / % ** & | ^ << >>`、一元 `+ - ~` 和六种比较运算可以调用实例类型上的协议方法；增强赋值先尝试对应原位协议。`abs`、`pow`、`floordiv`、`length`、`contains` 和显式 `bool` 也支持各自协议。定义方法、调用、解析、格式化和结构编辑都使用现有语法节点。

```chaos example=language-user-operator
class Amount {
  var value:Int=0;
  fn __init__(self,value:Int){self.value=value;null}
  fn __add__(self,other:Int)->Int{self.value+other}
  fn __radd__(self,other:Int)->Int{other+self.value}
}
let amount=Amount(40);
[amount+2,2+amount]
```

结果 `[42,42]`。`NotImplemented` 是不可伪造的内置拒绝哨兵，其类型为 `NotImplemented`；它允许双向候选继续分派，不等同于异常。协议、回退顺序、严格返回契约及 Task 行为见[运算协议](OPERATORS.md)。

## 19. 用户迭代与数据源

对象可以通过 `__iter__` / `__aiter__` 定义获取游标的方式，通过 `__next__` / `__anext__` 推进游标。for、async for、collect 和 yield from 使用同一迭代调度，不为可视界面另建语义。

```chaos example=language-user-iterator
class Counter {
  var value:Int=40;
  fn __iter__(self){self}
  fn __next__(self){
    if self.value<42 {
      let value=self.value;self.value=self.value+1;
      {done:false,value:value}
    } else { {done:true,value:"finished"} }
  }
}
let cursor=iter(Counter());
[collect(cursor),next(cursor)]
```

结果 `[[40,41],{done:true,value:"finished"}]`。同一原始迭代器实例的适配别名共享推进状态，完成值不会导致再次请求模型。同步推进直接返回严格的 done/value 记录；异步推进返回 Task，等待后的结果才是该记录。完整工厂、双向通信、关闭和错误规则见[用户迭代器](ITERATORS.md)。

## 20. 条目访问与切片

方括号支持 `value[key]`、`value[start:stop:step]`；三个切片边界都可以省略。自定义对象收到原始键或不可变 Slice 值。`value[key] = item` 可调用用户写协议，`delitem(value,key)` 调用删除协议。原生 List、Record、String 仍使用值式更新；不能借这些语法直接修改宿主容器。

```chaos example=language-item-slices
class Window {
  let items=[10,20,30,40,50];
  fn __getitem__(self,key){self.items[key]}
}
let window=Window();
[window[-1],window[1:5:2],window[::-1],slice_indices(slice_key(null,null,-1),5)]
```

结果 `[50,[20,40],[50,40,30,20,10],[4,-1,-1]]`。切片语法产生严格的 Slice，步长零会报错。完整协议、异步结果和语法版本规则见[索引指南](INDEXING.md)。

## 智能体定义与动态角色

Agent 保持一元 Function 调用协议。`agent_from`、`agent_info`、`agent_source` 是普通内置函数，支持值传递、词法遮蔽与模块组合，不增加 AST 关键字。详细语义与可运行工厂示例见[智能体定义值](AGENT_VALUES.md)。

## 团队函数与关系

`Team` 是受控类型，也满足 Function；`team_from` 等是普通内置函数。Team 定义中的父子能力范围影响 Context 的实际工具授权，角色调用返回冷 Task。无需新增 parser 关键字，普通模块、宏和调用 AST 即可组合，详见[团队指南](TEAMS.md)。

## Program 与自编程（0.22）

`Program` 是不可变、可调用的一等类型。闭合源码包可以被自身反射、结构编辑、作为冷 Task 执行并通过测试后采用。完整 API、权限、版本和预算语义见 [语言内自编程](SELF_PROGRAMMING.md)。这不新增按字符串内容解释意图的特殊规则。

## Process：持久后台函数

`Process` 是受控的一等 Function，调用创建启动冷 Task。导出函数通过严格 `{state,output,done}` 协议产生下一状态，实际执行由相同 Engine 在新源码快照中完成。定义、完整内建和恢复语义见[持续函数指南](PROCESSES.md)。

## 持久智能体会话

AgentSession 是受控 Function，冷 open/load 获得当前项目句柄，调用返回冷 Task 及幂等回执。语言签名、身份、历史、分叉、压缩、关闭与协调见[会话规范](SESSIONS.md)。它沿用普通函数组合、宏与 Task 调度，不新增依赖提示词识别的语法。


## 0.25 环境值与视觉输入

新增 Computer、Observation、ComputerAction、ObservedInput 及冷环境效果，普通函数控制观察、行动和验收。实际首个后端为无头 Chromium；见[完整规范](COMPUTERS.md)。

## 程序历史效果

`program_history(after=0,limit=50)`、`program_revision(version)` 返回冷Task；后者await得到受当前解释器拥有的Program。历史origin说明来源，采用仍须匹配当前运行基准与授权。它们是普通内置函数调用，没有新增语法或关键词；见[程序历史](PROGRAM_HISTORY.md)。

## 资源作用域（0.28）

`with manager as name { body }` 与 `async with manager as name { body }` 是保留 body 完成值的表达式；逗号头部按嵌套作用域执行。`as` 必须显式书写，with 保留普通标识符兼容性。类通过 `__enter__`／`__exit__` 或异步对应协议定义行为；退出接收错误记录或 null，只有普通错误能由 true 抑制。返回、循环控制和生成器关闭仍传播，硬取消与资源限制沿用 finally 的跳过策略。语法、例子和协议边界见[资源作用域](CONTEXTS.md)。

## 并发汇合与状态反射

0.30 新增 `TaskGroup` 类型、`task_group()`、`task_group_info(group)` 与 `group.spawn(task)`；沿用原生 async with 语法，正常退出等待成员，失败抢占组体并排空。`task_info(task)` 读取真实 Task 元数据，不启动或观察其结果。gather 保持输入顺序返回结果，并在错误时及时取消、完整收束成员，保留硬控制优先级。详见[并发任务](TASKS.md)。

## 原生消息通道

0.31 增加 `Channel` 类型与 channel／channel_send／channel_receive／channel_close／channel_info。现有 call、await、async for 和类型注解即可表达通信，无需新增语法节点。发送与接收是冷 Task；Channel 可被异步迭代，多消费者分配消息，关闭保留已缓冲数据。Channel 成为内建类型名，已有以此名称定义 class/data 的程序需要重命名。完整提交、取消、所有权和容量规则见[通道规范](CHANNELS.md)。

### 原生计时与完成等待

`sleep(seconds)` 和 `task_wait(tasks,until="first",timeout=null)` 返回冷 Task，复用普通调用及 await AST。后者返回 `{done,pending,timedOut}`，成员仍是活 Task；显式 await 成员取得结果。期限不增加运行预算，等待不隐式转移或取消成员，见[完整等待语义](WAITS.md)。

## 一等 ChannelCase 与选择

0.33 增加内建类型 ChannelCase 和 send_case／receive_case／case_info／channel_select。候选是不可变、可复用的本轮描述；选择是只提交一个分支的冷 Task。普通调用、类型注解及 await 已能表达，无新增解析节点。ChannelCase 成为内建类型名，已有同名 class/data 需要重命名。完成值、错误、偏置和生命周期见[选择规范](SELECT.md)。

## 可迭代函数组合

生成器、用户同步迭代器、函数星号展开与 apply/map/filter/fold/any/all 使用同一调度器协议。智能体回调保持实际能力和预算，async 回调仍返回显式等待的冷 Task；短路和错误保留已消费的游标位置。参见[可迭代值与函数组合](FUNCTION_COMPOSITION.md)。按需组合使用[std/iter](STREAMS.md)的真实语言函数；它返回单次生成器，不改变全局 map／filter 的急切行为。

## 推导表达式

0.35 支持 `[E for x in xs if condition]`、`{K: V for x in xs}`、`(E for x in xs)` 和 `async (E for x in xs)`；可以混用后续循环与条件子句。绑定只在推导内部可见，条件严格 Bool，记录键严格 String。同步／异步迭代、惰性时序及控制边界见[推导式参考](COMPREHENSIONS.md)。

## 原生解构绑定

支持 `let [a, b] = value`、`var {x: x, ..rest}: Record = value`，以及循环／推导中的结构目标。0.51 的 `[a, b] = value` 更新已有可变绑定：右侧、完整匹配和全部原契约检查通过后一次提交。`with manager as [a,b]`／异步形式在进入成功后匹配并发布只读局部名字，匹配失败仍进入普通退出流程。完整匹配和当前层冲突检查成功后才一次提交绑定；失配抛出 `match_error`。整值注解不成为可变叶子的契约。语法、作用域、标签模式、宏与兼容边界见[解构绑定](PATTERN_BINDINGS.md)。

## 结构化参数契约

`fn sum(pair as [left, right]: List) { left + right }` 可用 `sum([20, 22])` 或 `sum(pair=[20, 22])` 调用。`pair` 保留整值，`left/right` 是只读词法绑定；不能通过关键字标签绑定这些叶子。模式适用于位置专用、普通、仅关键字及剩余参数；全部形状匹配成功才进入主体。异步与生成器调用在返回冷句柄前也检查模式。默认值仍在定义时求值，模式在调用时检查。完整规则、宏与 IR 兼容见[参数解构](PATTERN_BINDINGS.md#函数参数中的模式)。

## 0.53 元组与复合键

Tuple 是区别于 List 的不可变有序值，支持 `()`、`(value,)`、`(a,b)`、`tuple(items=[])`、严格契约、迭代、切片和纯结构模式。`(value)` 与生成器表达式含义不变。新方言的 `object[row,column]` 生成原生 Tuple 键；用户条目协议定义多维含义。运行结果使用无行为的 `$tuple` 数据描述，严格 JSON 工具入口仍需显式转换。完整规则、源码与 IR 示例见[元组规范](TUPLES.md)。

## 类字段模式

`C::{field: pattern, ..rest}` 匹配 C 或其子类实例的有效存储字段，允许额外字段；`C::{}` 是名义类型测试。它支持所有结构绑定位置。Record 模式不变，默认精确字段匹配。类模式不执行 getter 或用户协议；完整语法、剩余项、权限和组件实例见[类字段模式](CLASS_PATTERNS.md)。

## 0.57 集合与集合推导

Set 是原生不可变集合，支持 `{a,b}`、`{*items,value}`、`set_of()`、集合推导与 `| & - ^` 运算；`{}` 保留 Record 含义。成员严格区分 Bool／Int／Number；运行时接受原生键、守约 Instance 与递归 Tuple／Set。纯模式保持无钩子，协议集合检查所有权和能力；迭代按插入顺序，普通相等与顺序无关。宏、异步来源、Channel、自编程和 IDE 共享实现，见[完整集合规范](SETS.md)。

## 程序编译分析

`program_compile(value: Program) -> Record` 是原生函数，读取受控程序快照，返回 `chaos.compilation/1` 编译报告，含依赖身份、结构效果和明确限制。它运行受限宏展开但不执行目标程序；共享运行预算与所有权约束保持生效，详见[编译规范](COMPILER.md)。

## 异步 GPU 数值能力

`gpu_map`、`gpu_matmul`、`gpu_devices` 使用普通 name/call/await 语法和既有 Task，无新增字符串脚本或第二套解释器。映射的普通 Function 经过实际 AST／绑定验证后转成受限 Float32 数值 IR；外层 async fn 可成为 AgentTool 或 Team 角色。它是显式精度合同，原普通整数函数不被重写。安装、设备、冷任务及取消见[GPU 指南](GPU_COMPUTE.md)。

## 纯函数的 Metal 编译

`gpu_map(fn, values, *, device="auto", compiler="tensor")` 保留原语法与五字段完成值。显式 compiler="metal" 要求 MPS 编译执行，compiler="auto" 按实际后端和编译可用性处理。`gpu_kernel_info(task)` 只读查询同轮原 gpu_map Task 的编译证据，不启动任务、不标记为已观察。旧任务、其他上下文、录制范围和非映射 Task 均按语言合同拒绝。详见[Metal 后端](GPU_METAL.md)。

0.69 新增 Fraction、Complex、Rational、Real、Numeric 契约和普通构造器；历史 Number、Int 除法、严格 Bool 与相等不变。精度、运算及无损数据边界见[数值指南](NUMBERS.md)。

0.70 的 Decimal 加入 Real／Numeric，Number／Rational 历史合同不变。精度与舍入属于逻辑 Task／Generator，完整有限数值及生命周期见[Decimal 指南](DECIMALS.md)。

## 0.71 原生情况与动态恢复

[情况处理与恢复出口](CONDITIONS.md)提供不可变 Condition、handler_bind、restart_bind 和真正展开的 restart_case。它使用原有函数/call/with 语法；情况数据不携带动态权限，真实 Task／Generator 的恢复范围互相隔离。直接 async 恢复回调透明执行，回调内部普通 async 调用保持冷 Task。


## 0.72.0：原生任务局部上下文

[任务局部上下文与动态绑定](DYNAMIC_CONTEXTS.md)说明 ContextVar、ContextToken、ContextScope 和 Context 的实际语言语义：普通任务/生成器创建时浅复制绑定，词法与透明协议调用共享上下文，选定 async 回调原 AST 执行，退出与取消恢复绑定。上下文可保存合法原生语言值，不能扩大工具权限。源码/IR、IDE、CLI、SDK 与 HTTP 共用实现；复制是有界 O(n)，宿主 GC 未被替换。本增量不表示完整 Python/Lisp 生态或自托管已经完成。


## 0.73 原生整数优化

[原生整数 DAG 优化](SCALAR_OPTIMIZER.md)为现有 LLVM 标量区域提供实际常量折叠、CSE 和数据依赖调度。原逻辑检查点、源位置、Task/Context 和效果边界保持不变；普通编译函数可直接成为 AgentTool/Team 的函数实现。compile_optimization_info 是共享 NativeSpecs 的只读查询，旧 compile_function/compile_info 不变。它不表示整程序指令重排、链接删除、原生循环/AOT、自研 GC 或完整 Python/Lisp 能力已经完成。


## 0.74 可变控制流中的原生区域

[可变控制流中的原生计算区域](NATIVE_CONTROL_REGIONS.md)将显式 auto-control/native-control 配置接入原 compile_function 两参数接口。原 Function/AST/Cell、同步迭代器、循环耗尽 else、break/continue/return 与真实 CHECKPOINT 保持不变；选中表达式逐次读取当前值并进入受守卫的 LLVM 内核。增强赋值只编译 RHS；严格 Bool、短路与相等使用原协议。旧 auto/native/interpreter 的范围、默认行为、compile_info 12 字段和优化查询保持兼容。此实现不是整个循环 CFG 的机器分支、可恢复 PC ABI、AOT、自研 GC、完整 Python/Lisp 或自举。
