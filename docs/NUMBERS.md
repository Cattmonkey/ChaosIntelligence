# 原生精确数值与复数

0.69 把不可变 Fraction 和 Complex 接入 Chaos 自身的求值器、函数调用、运算协议、宏、调度器、集合与工具链。没有 Python 表达式代理或独立网页解释器。构造器是普通函数调用，因此 IDE、图形 AST 参数编辑、无界面源码、Program 自我修改都使用相同程序。

## 类型与构造

| 调用／契约 | 合同 |
| --- | --- |
| `fraction(numerator:Int=0, denominator:Int=1)` | gcd 约分、正分母、零为 0/1；分母不能为零；不接受 Bool／float |
| `complex(real:Real=0, imag:Real=0)` | 两个不可变实数部件；不接受嵌套 Complex 或 Bool |
| `Rational` | Int 或 Fraction |
| `Real` | Int、原 float Number、Fraction 或 Decimal |
| `Numeric` | Real 或 Complex |
| `Number` | 保留原 Int／float 契约，不接受 Fraction／Complex |

Fraction 即使分母为 1 也保持 Fraction；Complex 即使虚部为零也保持 Complex。Complex 的整数 Fraction 部件归一为 Int；无 Decimal 部件且任一部件是 float 时，两部件都显式提升为有限 float；Decimal 部件只与 Decimal／Int 同域，float／Fraction 需要显式 decimal 转换。`numerator`／`denominator` 和 `real`／`imag` 是只读成员；`conjugate(value:Numeric)` 是普通内建函数，实数返回原值，复数反转虚部。std/math 同样导出构造器、共轭和 codec。

```chaos example=numeric-exact
import "std/math" as M;
let q:Rational = M.fraction(1, 3);
let result = q + 2;
let total = sum([q, fraction(1, 6)]);
[result.numerator, result.denominator,
 total.numerator, total.denominator,
 fraction(-6, -8) == fraction(3, 4)]
```

## 运算与精度

| 操作 | 结果／边界 |
| --- | --- |
| Fraction 与 Int 的 `+ - * /` | 精确 Fraction；结果整数也不降为 Int |
| Complex 的 `+ - * /` | Complex；精确部件保持有理值，混 float 返回 float 部件 |
| Fraction／Complex `**`、pow | 指数必须为 Int；支持负数和零；零的负幂报错 |
| Fraction floordiv／`%` | floordiv 返回 Int；余数遵除数符号；Complex 拒绝这两项 |
| `< <= > >=`、min／max／sort | Real 精确比较，包括 Fraction 与 float 的真实二进制值；Complex 无排序 |
| floor／ceil／round | Real 到 Int；Fraction 使用整数算法，中点取偶数 |
| sum | Numeric 列表；共享数值提升；空列表返回 Int 0 |
| number／非 Decimal sqrt／abs(Complex) | 明确的有限 float 转换／近似；Complex magnitude 保留 Number 合同 |
| sqrt(Decimal)／abs(Decimal) | Decimal；sqrt 使用当前精度及固定 half_even，见[Decimal](DECIMALS.md) |

纯 Int／float 运算的历史行为保持，例如 `1 / 2` 返回 float Number。Fraction 与 float 混合算术显式选择有限 float；不能把这个结果当作精确分数。Complex 除法统一以有限部件的有界比值计算商，最后一次转换浮点部件，避免大数平方的无必要溢出；其他浮点操作仍受有限中间结果约束，并不提供任意精度浮点上下文。

相等依旧对类型敏感：`fraction(1) != 1`，`complex(1) != 1`，`complex(1) != complex(1.0)`。规范化后的同型分数／复数按部件相等。Set 的纯键规则采用相同身份和规范化，不调用用户 hash／比较协议。Complex 的负数或零次幂保持原来的 exact／float 部件精度。

```chaos example=numeric-complex
let phase = complex(fraction(1, 3), fraction(2, 5));
let energy = phase * conjugate(phase);
[energy.real == fraction(61, 225), energy.imag == 0,
 phase / phase == complex(1), complex(0, 1) ** 4 == complex(1),
 round(fraction(5, 2)), floor(fraction(-7, 3))]
```

严格 Bool 保持原合同：新数值不能隐式进入 if／while／逻辑运算，`bool(fraction(...))` 也被拒绝。索引、移位和位运算仍要求 Int，不因 Fraction 的分母为 1 自动转换。Complex 不支持 floor、余数、排序或 number 转换。

## 无损数据边界

`numeric_encode(value:Numeric) -> Record` 与 `numeric_decode(value:Record) -> Numeric` 只处理单个数值；decoder 不遍历任意容器。外层必须恰好一个 `$numeric` 字段，内部只接受指定字段：

| kind | 数据 |
| --- | --- |
| Int | `value` 为规范十进制 String |
| Number | `value` 为有限 float 的规范 repr String，例如 `"1.0"` |
| Fraction | `numerator`、`denominator` 为规范十进制 String；decode 按构造器约分 |
| Complex | `real`、`imag` 为两个 Real scalar codec Records；不能嵌套 Complex |

JSON／SDK／HTTP／工具效果遇到原生 Fraction／Complex 会投影为这个显式记录；普通 Int／float 的历史 JSON ABI 不改。跨 JSON 持久化需要调用方显式 decode。`parse_json` 永远把标记作为普通 Record，不根据文本关键词自动建立数值；嵌套 DTO 由程序明确选择字段并 decode。str 沿用通用 JSON 表示，不是分数文字解析器。浏览器精确显示大整数时，应使用 codec 十进制字符串。

```chaos example=numeric-codec
let original = complex(fraction(7, 11), -2);
let saved = parse_json(json_stringify({value:original}));
let restored = numeric_decode(saved.value);
[type_of(saved.value), restored == original,
 numeric_decode(numeric_encode(1)) == 1]
```

## 宏与智能体函数

闭合编译期宏共享同一个有界数值代数。syntax_literal 生成普通构造调用，并在仅编译器创建的展开节点上捕获内建构造器身份；调用方同名变量、参数或函数不能截获它。原始宏源码和 IR 仍是保存、格式化、重命名和结构编辑的权威；不把展开后的内部身份序列化为用户可伪造的 IR 字段。分析、补全和格式化只检查语法与元信息，不执行构造器或用户回调。

```chaos example=numeric-macro
macro exact() {
  syntax_literal(complex(fraction(1, 3) + fraction(1, 6), 1) ** 2)
}
let expected = complex(fraction(-3, 4), 1);
let fraction = fn() { 0 };
let complex = fn() { 0 };
exact!() == expected
```

普通 async 函数、生成器、Channel、AgentTool 和 Team 角色可以接收或返回这些原生数值；仍使用原来的返回契约、所有权、权限收窄、结构取消和预算。持久化 Program／Process、Swarm 的有限 JSON 快照需显式 codec Records；标准库不会自动解码数据，更不会改角色权限。工具效果不回滚。`compile_function` 的整数后端和 GPU Float32 后端仍只支持各自已列输入，不把 Fraction／Complex 静默降精度。

## 资源与兼容边界

每个公开整数、分子和分母最多 14000 bit；代数 scratch 中间比值最多 28002 bit。约分、交叉消去和幂循环共享 evaluator／宏检查点。精确幂的非单位指数绝对值最多 14000，还需满足结果资源限制；单位 Fraction 的大指数直接求值。超限报 ResourceLimit，而不是放任宿主分配。decoder 在 int 转换前限制字符串长度与规范形式，不允许非有限 Number、额外字段、宿主对象、闭包或 Task 冒充数值。

这一步增加原生数值能力，并不宣称完整 Python／Lisp 兼容。Fraction 只接受 Int，与 Python 接受更多输入的 [fractions 模块](https://docs.python.org/3.13/library/fractions.html)不同；Complex 和 Fraction 保留显式身份，也不同于 [Common Lisp rational 规范化](https://www.lispworks.com/documentation/HyperSpec/Body/t_ration.htm)。0.70 已提供[有限 Decimal 与上下文精度／舍入／信号](DECIMALS.md)。通用张量生态和完整标准库仍待实现，见[能力对照](PYTHON_LISP_CAPABILITIES.md)。

Decimal codec 使用 sign、coefficient String、exponent 封闭字段，保持末尾零／负零，见[Decimal 数据协议](DECIMALS.md)。
