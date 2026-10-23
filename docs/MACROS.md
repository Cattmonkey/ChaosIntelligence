# AST 宏与编译期语法

Chaos 宏接收和返回真实语法树，在程序执行前展开。普通函数接收已经求值的运行期值；宏参数保留为未求值的 `Syntax` 对象。宏不使用文本替换，不调用模型解释语法，也不读取运行期环境。本页描述当前有限的宏系统，不承诺 Scheme、Common Lisp 或其他语言的完整宏兼容性。

## 声明、调用与求值时机

```chaos example=macro-once
macro twice(value) {
  quote {
    let temporary = unquote(value);
    temporary + temporary
  }
}
let temporary = 100;
[twice!(21), temporary]
```

结果是 `[42, 100]`。`macro name(parameters) { body }` 只允许模块顶层，参数是没有类型、默认值或展开修饰的名字；本地调用写成 `name!(expressions)`，导入的导出宏写成 `Alias.name!(expressions)`，按位置传递固定数量的语法参数。普通 `name(...)` 仍是运行期调用。

宏体最后一个表达式必须得到 Syntax。`quote { ... }` 构造块语法；`unquote(expression)` 在模板中执行编译期表达式并插入其 Syntax 结果。这个块在运行期有自己的词法作用域，结果是最后一条语句的值。不能把普通数字直接交给 `unquote`，需要先用 `syntax_literal` 构造语法。

`twice!(tool("app.read", input))` 的参数不会在编译期调用工具；本例展开后的 `let` 使工具只在运行期执行一次。若模板重复插入同一参数，运行期也会重复求值。未插入的参数不会执行。宏应明确自己的求值次数和顺序。

模块内宏声明在展开前统一收集，可以在声明之前调用；宏名与其他顶层声明不能重名。宏不能作为普通运行期函数传递，也不支持具名宏参数、动态加载宏或运行时 `eval`。

## 卫生与显式依赖

模板中的 `let`、`var`、函数、类的词法绑定、形参、循环变量、catch 变量和模式绑定获得新的内部名字，避免捕获调用者变量。插入的调用者语法保持自己的名字和来源位置。函数形参的内部绑定名改变，但对外具名参数标签不变。

```chaos example=macro-closure
macro makeAdder(value) {
  quote {
    fn(x: Int, *, bonus: Int = 1) -> Int {
      unquote(value) + x + bonus
    }
  }
}
let x = 40;
let add = makeAdder!(x);
[add(x=1), add(1, bonus=2), x]
```

结果是 `[42, 43, 40]`。闭包里的形参 `x` 不会遮蔽通过 `unquote(value)` 插入的调用者 `x`。

本版使用**封闭模板**：模板内运行期名称必须由模板自身绑定，或通过 `unquote` 显式传入。即使模块已经声明了 `helper`，`quote { helper(1) }` 也会报错；写成参数化的 `macro invoke(helper, value) { quote { unquote(helper)(unquote(value)) } }`。这个规则同样适用于内建函数、工具入口和 Agent，可避免调用处意外遮蔽隐式依赖。编译期 `let syntax = ...` 的名字也必须通过 `unquote(syntax)` 插入。

模板可以产生普通函数、同步／异步生成器、异步函数、条件、同步／异步循环、错误处理、运行期智能体声明、类和成员赋值。当前不能产生 `import`、`export`、`data` 或新的宏声明；不能直接嵌套生成另一个 `quote` 节点。类型契约可以使用内置契约名或模板内部显式绑定的类型名称，包括类自身及 `T.C` 这样的限定类型。外部自由类型名仍被拒绝；可以用 `let T = unquote(type_arg)` 显式引入依赖，再写 `value:T`。模式匹配支持字面量、绑定、列表、记录和通过模板内别名显式注入的 data 构造器模式。可以把这些已有表达式作为语法参数传入。

类模板保留公开字段名、方法名和具名参数标签；类名、方法接收者及局部词法绑定同步重命名。成员默认值在类声明处按源顺序求值，可以读取之前的成员；方法主体捕获外层环境和自身类名，通过接收者访问字段。`class` 与成员写入只允许作为生成的运行期语法，不能在宏纯求值阶段执行。完整例子见[对象规范](OBJECTS.md#宏生成对象)。

类成员和函数参数默认表达式没有外围 return、循环控制或 yield 目标；宏展开会在效果执行前检查这些边界。await 的许可来自声明位置：顶层和 async 函数允许，普通函数不允许；生成的方法主体再按自己的函数形式建立新作用域。

## 装饰器模板

quote 可以生成函数、类、Agent 及类方法的装饰声明。装饰器表达式在声明前的模板环境中解析，声明完成后才把新的卫生绑定提供给后续语句。类成员标签保持公开；同名属性 accessor 更新保留此前 Property 的访问路径，由运行时验证真实更新身份，不根据 `property` 或 `setter` 的拼写放宽重复规则。

装饰器表达式中的宏仍在编译期展开；装饰器调用只在运行期执行。宏纯求值不能执行 decorated 声明，也不能借装饰器调用 Agent 或工具。装饰器表达式和默认值一样没有外围 return／循环／yield 目标，await 许可来自声明位置；普通函数不能借宏注入 await。语法与完整卫生例子见[装饰器规范](DECORATORS.md)。

## 编译期纯计算与宏组合

宏体有独立的纯求值器，支持字面量、列表、记录、索引、`let`、`do`、`if`、标量运算、普通 `fn`／命名函数以及函数调用。函数支持闭包、递归和既有参数绑定规则，受编译预算限制。三个辅助函数均只接收一个位置参数：

| 编译期函数 | 行为 |
| --- | --- |
| `syntax_literal(value)` | 将有界纯数据转换为字面量、列表或记录语法；拒绝函数和 Syntax 值 |
| `syntax_kind(syntax)` | 返回根节点种类，如 `literal`、`binary`、`call` |
| `syntax_value(syntax)` | 读取单个 `literal` 节点的值；不会计算 `1 + 2` 等表达式 |

```chaos example=macro-pure
macro total(value) {
  fn sum(n, *, acc = 0) {
    if n == 0 { acc } else { sum(n - 1, acc=acc + n) }
  }
  if syntax_kind(value) == "literal" {
    syntax_literal({result: sum(syntax_value(value)), checked: true})
  } else {
    value
  }
}
[total!(3), total!(1 + 2)]
```

结果是 `[{"result": 6, "checked": true}, 3]`。第二个参数是二元表达式语法，因此按原样保留，运行时才得到 `3`。

```chaos example=macro-compose
macro addTwo(value) {
  fn compose(f: Function, g: Function) -> Function {
    fn(x) { f(g(x)) }
  }
  fn addOne(x) { quote { unquote(x) + 1 } }
  let composed = compose(addOne, addOne);
  composed(value)
}
addTwo!(40)
```

结果是 `42`。这是编译期函数组合；宏体内部的函数不进入运行期。宏体本身使用末尾表达式，直接 `return quote { ... }` 是语法错误；内部普通函数可以使用 `return`。

```chaos example=macro-nested
macro inner(x) { quote { let saved = unquote(x); saved + 1 } }
macro outer(x) { quote { inner!(inner!(unquote(x))) } }
let saved = 40;
outer!(saved)
```

结果是 `42`。展开结果中的宏调用会继续展开，共享整个包的编译预算。宏体本身不提供 `var`、循环、`rec`、Agent、工具、工作流、await 或运行期内建函数；需要重复计算时可使用有界递归纯函数。效果只能出现在生成的运行期语法里，随后仍受项目能力检查。

未调用的宏体会接受通常的语法检查，但不会执行纯求值器或检查所有模板自由名称；对应错误在宏被调用时才报告。编译期 `if` 未选择的分支也不会执行。不能因此把“包检查通过”解释为所有宏在所有未来参数下都能展开。

## 模块、IR、控制流与错误

顶层宏默认私有，可通过 `export {macroName}` 显式导出。导入后使用 `Alias.macroName!(...)`；只有一级、静态的顶层 `import` 别名支持这种限定调用。它是编译期命名空间，不会求值同名运行期对象，因此局部 `let Alias = ...` 不改变宏解析。`Alias["macroName"]!`、`Alias.nested.macroName!`、把模块记录赋给另一个变量再用该变量调用宏，均不支持。

例如将以下内容保存为 `macros.chaos`：

```chaos
macro increment(value) { quote { unquote(value) + 1 } }
macro addTwo(value) { quote { increment!(increment!(unquote(value))) } }
fn answer() { addTwo!(40) }
export {addTwo, answer}
```

同一程序包的入口 `main.chaos`：

```chaos example=macro-modules
import "macros.chaos" as M;
[M.addTwo!(40), M.answer(), keys(M)]
```

结果是 `[42, 42, ["answer"]]`。`increment` 是定义模块的私有宏，导出的 `addTwo` 仍能使用它；外部 `M.increment!(...)` 被拒绝。宏不成为运行期值，`M.addTwo(...)` 也不是普通函数调用。`ProgramBundle.exports` 仍只列运行期导出，`macro_exports` 单独列宏导出；模块检查摘要的对应字段为 `exports` 和 `macroExports`。

模板生成的 `helper!(...)` 在宏的定义模块解析，包括该模块自己的导入别名；调用者存在同名宏或别名不会改变其含义。通过 `unquote` 插入的参数语法保留调用者的宏命名空间。例如库中的 `addTwo` 展开 `M.addTwo!(increment!(40))` 时，内部两次 `increment!` 使用库的私有宏，参数里的 `increment!(40)` 使用入口定义的宏。这个规则也适用于多次转发和嵌套展开。普通运行期自由名称仍遵守封闭模板规则，函数、工具或 Agent 需要显式参数注入。

宏可以在同模块声明之前被调用；链接阶段先收集所有顶层宏和导入。模块依赖仍必须是封闭、无循环的程序包，编译阶段不读取额外文件或执行模块效果。导出运行期导入别名不会自动再次导出其中的宏；需要转发时声明包装宏，如 `macro public(x) { quote { M.addTwo!(unquote(x)) } }`，然后导出 `public`。

REPL 每次提交建立自己的编译包，宏声明不作为会话的运行期绑定跨提交保存。

`ProgramBundle.trees` 保留原始宏声明、调用和 quote/unquote；源码格式化、结构 IR 和版本编辑使用这棵原始树。`ProgramBundle.expanded_trees` 是单独的执行树，Evaluator 和 Session 使用它运行。定义模块和调用者的解析身份保存在展开节点的私有元数据中，不写入原始 IR。编译期步骤不是 Python 原生编译，也不覆盖原始源码。

展开后再次检查控制流位置：`return` 必须位于函数内，`break`／`continue` 必须位于当前函数的循环内，`yield` 必须位于同步或异步生成器内，`await` 和 `async for` 只能位于模块顶层、异步函数或异步生成器内。将调用者语法搬到新函数或默认参数位置时也检查这些限制。所有模块先完成展开和检查，然后才开始运行效果。

宏错误的 `code` 为 `macro_error`；展开期间的错误位置指向调用处，详情在可用时包含宏定义、模板来源和展开链。由模板生成的运行期表达式使用宏调用位置，并保留 `macroOrigin` 来源信息；插入参数保留调用者原来的来源位置。预算耗尽使用不可被语言 catch 吞掉的 `resource_limit`。

| 整个程序包共享的编译上限 | 默认值 |
| --- | --- |
| 纯求值、复制、展开与检查的总工作步数 | 50,000 |
| 创建的语法节点数 | 50,000 |
| 宏调用数 | 1,000 |
| 编译和展开内部嵌套深度 | 64 |
| 编译时间 | 2 秒 |
| 单个列表／记录 | 10,000 项 |
| 单个字符串／整数 | 250,000 字符／14,000 bit |

这些预算与[运行期预算](ADVANCED.md)分开。无限递归宏和不断增大的展开会明确失败。没有宏及编译期节点的模块直接沿用原始执行树，保留既有语言行为。

智能体如何与宏、生成器和 Task 组合，见[智能体语言模型](AGENT_LANGUAGE.md)和[异步效果](EFFECTS.md)。

## 生成用户描述符

用户描述符的方法名 `__get__`、`__set__`、`__delete__`、`__set_name__` 和成员标签都属于公开协议，不进行卫生重命名；模板内部的类名、方法接收参数和其它词法绑定仍会重命名。命名 hook 得到实际公开成员标签。生成的类可以照常导出，生成宏也可通过已有 `Alias.name!(...)` 规则跨模块导出。

编译期纯求值不会实例化类或触发访问／命名 hook，也不提供运行期 delattr。把删除调用放入 quote 时，将 delattr 作为宏参数显式 unquote 注入；原始源码／IR 仍保留普通调用节点和宏声明，展开后才运行效果。[用户描述符协议](DESCRIPTOR_PROTOCOL.md#宏模块与阶段)提供完整可执行模板。

## 生成运算协议与 NotImplemented

宏可以生成 `__add__`、`__radd__`、`__eq__` 等普通方法；协议名是公开成员标签，类名与形参保持卫生规则。新运算与显式 bool／length／contains 只在生成后的运行期解释器中调用，不把 Agent、对象或描述符引入纯展开。

NotImplemented 是运行期单例，封闭模板应通过参数传入 `NotImplemented` 的 Syntax，再 unquote 插入。可以在模板中使用 `-> NotImplemented` 契约；不能把内部单例编码为 syntax_literal 字面量或从 JSON 标记恢复。纯阶段仍只提供原来的三个 syntax_* 构造器，不新增运行期内建函数。完整可执行例和双向回退规则见[对象运算协议](OPERATORS.md#宏只生成运行期协议)。

## 生成迭代协议

用户 Iterator／AsyncIterator 不需要新的宏 AST。quote 可以组合已有 class、普通／异步／生成器方法、for、async for 和 yield from；`__iter__`、`__next__`、`__aiter__`、`__anext__` 以及可选 send／throw／close 协议的公开标签保持原名。模板中的类名、方法形参和循环变量仍卫生重命名，跨模块宏和调用者 unquote 身份规则不变。内置 Iterator／AsyncIterator 契约可出现在生成后的代码中。

运行期 iter／aiter／next 及消费、发送、关闭函数仍不属于编译期纯白名单，编译不会推进游标、调用描述符或触发 Agent。需要在封闭模板中使用这些函数时，显式传入其 Syntax 并 unquote；也可只生成协议类，让调用者在运行期消费。syntax_literal 不接受活动 Iterator／AsyncIterator，普通 JSON 描述不能复活句柄。完整可执行模板、严格完成记录和生命周期见[用户迭代器](ITERATORS.md#宏生成协议)。

## 索引、切片与历史成员语法

新源码的方括号读取／赋值使用 subscript／set_item；slice 的 start／stop／step 是三个可空表达式。quote 会遍历所有实际边界，保留省略位置，检查 return／yield／await 等控制作用域，并保存来源。协议标签 `__getitem__`／`__setitem__`／`__delitem__` 和 Slice 字段名不卫生重命名，方法形参与模板变量仍重命名。旧 index／set_member 继续表示成员访问，显式写法为 `object.[key]`；不能将它们格式化成可能触发新协议的方括号。

宏纯阶段允许内置 List／Tuple／String／Record 的新方括号读取、负索引及切片。这条路径只使用原生纯数据算法，不触发描述符或用户协议。纯环境仍不提供 getitem／setitem／delitem／slice_key／slice_indices 等运行期函数，syntax_literal 也拒绝 Slice 对象及嵌套包含它的容器。只有索引位置中的 slice AST 参与此纯算法，不把受控值直接嵌入源码字面量。

按文件的历史语言版本在宏展开之前决定解析方式，宏处理已经区分的节点，不猜测文本标点。跨模块传入的 Syntax 保留原节点种类和调用者身份；不同方言的模块可以在同一程序包中组合。完整可执行例与项目迁移规则见[索引与切片](INDEXING.md)。

## 结构目标与构造器别名

卫生模板中的结构声明、循环、推导和资源绑定使用逐叶新身份；来源、契约及构造器都使用绑定前环境。纯编译期支持 `let` 结构绑定及同步推导的原生结构匹配，不执行可变状态、标签构造器或运行时协议。构造器模式可通过模板内显式别名注入，模板内 `match` 同样适用。批量赋值的卫生遍历复用已有模板身份，不能把写入转换成新声明；自由目标被拒绝，调用者可显式注入整个赋值块 Syntax。宏纯阶段仍禁止赋值和资源效果。完整示例见[解构绑定](PATTERN_BINDINGS.md)。

## 参数模式与公开标签

函数模板中的 `name as pattern` 为 whole 参数槽和各叶子建立卫生身份，公开关键字标签 `name` 保持稳定。默认值与模式构造器按外层作用域展开，不捕获函数参数叶子。纯编译期函数复用无效果结构匹配，不执行运行时构造器、用户协议或智能体。示例与调用失败时机见[参数解构](PATTERN_BINDINGS.md#函数参数中的模式)。

## 增强赋值与数值运算

纯阶段允许原始 `** & | ^ << >>` 和一元 `+ ~`，共用数值类型与分配预算。纯阶段仍不允许赋值或运行期协议。quote 模板内的增强赋值在运行期执行；本地目标保持卫生性，显式 unquote 的调用者目标在展开后必须是合法赋值位置，否则在任何模块效果前报错。见[示例与完整规则](AUGMENTED_ASSIGNMENT.md)。

## 位置专用槽与宏卫生

宏内部的纯 fn 函数和 quote 生成的四类运行期函数均支持 `/`。模板参数槽的标签保留，词法身份仍独立生成；绑定器先按模式区分可具名参数与私有槽，再把值安装到卫生名字中。同名关键字作为 `**` 数据保留，不会被宏改成内部名字。

```chaos example=posonly-macro
macro make() {
  quote { fn(value, /, **options) { [value, options] } }
}
let value = 100;
let consume = make!();
[consume(42, value="metadata"), value]
```

结果为 `[[42,{value:"metadata"}],100]`。位置专用默认值仍使用定义处作用域；模式叶子仍是新词法绑定。共享参数验证器在执行默认值或函数体前检查完整参数顺序。该特性不扩展纯宏阶段的效果权限。

## 编译期元组

纯宏支持 Tuple 字面量、纯模式、严格相等、连接、索引及有界推导；`tuple(items=[])` 在纯阶段只接受 List、String、Tuple、Set，不能运行用户迭代器。`syntax_literal` 生成原生 tuple AST，拒绝元组内的函数或 Syntax；`syntax_value` 仍仅接受标量字面量。模板中的 p_tuple 模式、赋值与资源变量遵守卫生重命名，复合 Slice 键遵守与运行期相同的 AST 位置约束。见[元组宏示例](TUPLES.md#宏ide-与自编程)。

## 正常完成模板

quote 可以生成循环 else、try/catch else 及外层 finally。loop_else／try_else 通过原 AST 遍历展开，循环目标和 catch 名称只在自己的 body／handler 内卫生绑定，otherwise 使用外层作用域。展开后的控制检查不把 otherwise 算入已经结束的循环；顶层 break、越过函数或推导式边界的 return/continue 会在运行前拒绝。纯宏求值器的支持子集不因此扩大，运行期形式仍置于 quote，见[完成分支示例](CONTROL_COMPLETION.md#宏与-program-自修改)。

## 异常模板

quote 支持 try_cases 的有序模式／守卫／处理块和零字段 rethrow。每个候选模式有独立卫生绑定，构造器按外层模板环境解析；guard/body 共用本候选绑定。rethrow 在运行期访问动态错误，可放在辅助函数内；纯编译期求值子集没有因此扩展为完整运行期解释器。见[异常示例](EXCEPTIONS.md)。

## 引用类字段模式

quote 可生成 `C::{field: leaf}`；C 必须由模板内类声明或显式 `let C = unquote(type)` 提供。解析类路径使用叶子绑定前的环境，因此同名类别名与模式变量不会互相捕获；字段键不改名。纯宏执行不能读取运行时实例字段，必须在 quote 中生成运行时操作。示例见[类字段模式](CLASS_PATTERNS.md)。

## 原生 Set 的编译期语义

纯宏可构造 Set、执行集合运算、调用 set_of／set_add／set_discard／set_is_disjoint，并对纯 List／Tuple／Set／String 执行同步推导和集合展开；不调用运行时迭代器。严格纯键与运行时纯路径共享验证和预算；运行时的 Instance 哈希／相等协议不进入纯宏求值器。syntax_literal 为非空集合生成 set AST，为空集合生成 `{*[]}`，避免依赖可遮蔽的构造器名称；quote 中推导作用域卫生化。见[集合宏示例](SETS.md)。

0.69 纯宏可用 fraction／complex／conjugate／numeric_encode／numeric_decode 和共享数值运算；syntax_literal 保留构造器身份，调用方同名变量不能截获重建。边界见[数值指南](NUMBERS.md)。


## 0.76 Code 阶段桥梁

[一等 Code](CODE_VALUES.md)通过同一 Parser／宏／Program IR 构造可修改的不可变代码，再由 function_from(code, bindings) 在原调度器生成真实普通、异步或生成器 Function。显式绑定保留原语言值身份及权限，默认值仅在每次新函数构造时计算一次；旧函数与运行中 AST 不迁移。生成函数可组合 AgentTool／Team／std/swarm 和 JIT。IDE 对静态字符串提供参数提示，动态签名保守；0.77 已提供虚拟源断点与单步。模块 eval、reader 宏、continuation、自举及完整 Python／Lisp 能力仍未完成。

## 编译期多返回值

[多返回值](MULTIPLE_VALUES.md)的七个接口也可用于已有纯宏函数。`return_values`、`values_list` 与 producer 回调采用相同的零／单／多完成合同，普通宏绑定与语法构造参数取首项；显式捕获得到 List。回调保持纯函数与原宏预算，不能执行 Agent、工具、Task 或其他运行时效果。quote／unquote 发出的调用仍保留卫生身份。

```chaos
macro answer(){syntax_literal(multiple_value_call(fn(a:Int,b:Int)->Int{a+b},fn(){return_values(20,22)}))};answer!()
```

纯宏的 producer 保持编译期纯函数；运行时完成句柄查询属于原调度器，不进入宏阶段。

## 运行时控制模板

[控制提示](CONTROL_PROMPTS.md)的九个原语属于运行时控制／资源范围。纯宏不能调用它们；quote／unquote 可以通过显式语法依赖生成原生调用 AST，保持卫生身份、类型注解和来源。局部纯函数使用相同名称仍按其真实绑定调用，名称不会赋予运行时控制能力。

```chaos
macro escape_template(run,body){quote{unquote(run)(unquote(body))}}
import "std/control" as C;escape_template!(C.call_escape,fn(exit:EscapeContinuation){exit(42)})
```

[Robot 原生效果](NATIVE_ROBOTS.md)不在纯宏环境中执行。quote／unquote 可用显式依赖生成普通机器人调用 AST，闭包、类型注解、格式化、IR 和来源保持同一合同；RobotCommand 仍须在运行阶段由真实 owned 观察构造。

## 独立编译库的纯宏接口

源码包中的宏仍使用上面的 import／export、纯求值与卫生规则。发布无源码库时，`compile_objects(..., macro_interfaces=True)` 或 `compile_executable(..., macro_interfaces=True)` 生成 v3 对象；CLI 对应 `--macro-interfaces`。客户端通过 `libraries` 接收 v3 对象后自动采用 v3，可用原 `Alias.macroName!(...)` 语法调用库的导出宏，不解析或伪造库源码。客户端自己的宏也随该对象版本保留接口。

受验证的 `chaos.macro-phase/1` 胶囊保存原始宏声明 AST，包括私有帮助宏及定义处导入所需信息。运行时函数／类／数据仍由另一个已展开运行时树表达；客户端只展开自己的调用，不重新展开库的运行时树。定义处绑定、调用者参数、卫生改名、numeric／condition 纯构造和共享宏预算沿原 Expander 实现。胶囊不序列化活 Env、Function、宿主地址或权限。

链接／加载仅验证阶段数据，不求值宏或初始化模块。合法但纯解释器不支持的未调用宏体保留原延迟规则，实际调用才报错；宏不能因此调用模型、工具或设备。旧 v1／v2 对象只有宏导出名而没有实现时，外部调用明确报 macro_error，不把缺失接口当作空定义。

依赖按精确内容绑定；替换库后须重编客户端。mount_objects 还能显式重定位同名库及其登记的阶段来源，普通字符串与参数别名不作文本替换。完整对象、命名空间和验证边界见[编译运行时设计](COMPILER_RUNTIME_DESIGN.md)；SDK／CLI 使用见[SDK](SDK.md)。
