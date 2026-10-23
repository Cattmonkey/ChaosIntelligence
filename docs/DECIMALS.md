# Decimal 与逻辑精度上下文

0.70 把有限 Decimal、精度、舍入、信号和陷阱接入原生函数语言。源码、宏、图形 AST 编辑、AgentTool、任务和生成器仍使用同一个求值器。实现只调用显式构造的有界 `decimal.Context` 数学内核；不读取或修改宿主全局／线程上下文。

## 精确值与显式转换

`decimal(value=0)` 接收 String、Int 或 Decimal 时精确构造，不受当前精度舍入。只读 `sign:Int` 为 0／1，`coefficient:Int` 非负，`exponent:Int` 表示 coefficient × 10^exponent。保留末尾零和负零。`decimal_text(value:Decimal)` 输出保留这些信息的十进制文本；原 `str` 的通用 JSON 行为保持不变。

```chaos example=decimal-exact
let total:Decimal = decimal("0.10") + decimal("0.20");
let zero = decimal("-0.00");
[decimal_text(total), zero.sign, zero.exponent,
 decimal("1.0") == decimal("1.00"), decimal(1) == 1]
```

Decimal 加入 Real／Numeric；Number 仍是 Int／float，Rational 仍是 Int／Fraction。Decimal 与 Int 可直接运算；float／Fraction 必须显式 `decimal(...)`。Fraction 转换使用当前精度除法；float 转换保留真实二进制值并提交 FloatOperation 信号，不能把 0.1 假装成精确十进制 0.1。Bool 不接受。`number(decimal(...))` 是明确的有限 float 转换；严格 Bool、Int 下标规则不变。

同类型 Decimal 相等忽略末尾零和零的符号，Set 使用相同规范化键；与其他数值类型仍不相等。Real 排序按真实有理值比较，不使用舍入后的 float。

## 精度、舍入和信号

`decimal_context(precision=28, rounding="half_even", emin=-999, emax=999, clamp=false, traps=["InvalidOperation","DivisionByZero","Overflow"])` 返回当前执行周期拥有的配置模板。`with template as local` 安装独立的配置副本、清空其 flags，退出后恢复上一上下文；模板本身可重复使用。属性只读。

八种 rounding 为 ceiling、down、floor、half_down、half_even、half_up、up、05up。`decimal_quantize(value, quantum)` 按 quantum.exponent 舍入；sqrt(Decimal) 使用当前精度返回 Decimal，数学内核按 half_even 正确舍入，独立于 rounding 字段。一元 `+`、`-`、abs 和四则运算服从当前上下文。Int 幂用有界平方乘法，每一步遵守上下文；零次幂保留 Decimal／Decimal Complex 的数值域。

```chaos example=decimal-rounding
with decimal_context(precision=4, rounding="half_up") as local {
  let part = decimal(1) / decimal(7);
  let rounded = decimal_quantize(decimal("2.345"), decimal("0.01"));
  [decimal_text(part), decimal_text(rounded), local.flags]
}
```

九个标准 signal 为 Clamped、InvalidOperation、DivisionByZero、Inexact、Rounded、Subnormal、Overflow、Underflow、FloatOperation。flags 是 sticky 标准名称 List；traps 同样是名称 List。先提交本次所有 flags，再按 InvalidOperation、DivisionByZero、Overflow、Underflow、Subnormal、Inexact、Rounded、Clamped、FloatOperation 选择首个启用的 trap，抛出 `decimal_signal`。Rounded 可以没有 Inexact；quantize 的 subnormal 不提交 Underflow。

`get_decimal_context()` 返回当前逻辑上下文。`decimal_context_info(context=null)` 返回 precision／rounding／emin／emax／clamp／traps／flags 七字段 Record，列表按名称排序。`decimal_clear_flags(context=null)` 返回 Null，只允许清除当前实际上下文；其他 owned handle 可以读取，不能借此修改其他任务 flags。句柄不能跨 owner／执行周期使用。

```chaos example=decimal-signals
with decimal_context(precision=3, traps=["Inexact"]) as local {
  let code = try { decimal(1) / decimal(7) } catch error { error.code };
  let before = local.flags;
  decimal_clear_flags();
  [code, before, local.flags]
}
```

语言只接受有限结果。InvalidOperation／DivisionByZero／Overflow 导致 NaN／Infinity 时，即使关闭 trap 仍报 decimal_signal，且 flags 可观测。没有隐式 NaN、Infinity 或 truthiness。

## 异步生命周期

真正 Task／Generator 在冷创建时复制当前配置，flags 清空；以后 await／yield 不改它的逻辑上下文。兄弟任务不互相污染，flags 不自动合并回父任务。普通同步 Fn 及重入 native drive 分享调用者状态；TaskGroup 的词法 scope helper 同样分享。Agent 调用是实际子 Task，里面的普通 AgentTool Fn 分享 Agent Task 状态，返回父任务时不合并 flags。

```chaos example=decimal-tasks
async fn work(precision) {
  with decimal_context(precision=precision) as local {
    await sleep(0);
    [decimal_text(decimal(1)/decimal(7)), local.flags]
  }
}
let parts = await gather([work(3), work(6)]);
[parts, get_decimal_context().precision, get_decimal_context().flags]
```

上下文 native lease 使用原资源作用域，支持 ExitStack。正常退出、return／break／continue、异常、取消、生成器 close 和硬资源中止都执行内部恢复；硬中止不调用任意用户清理钩子。新执行周期获得默认配置和空 flags。callbacks 的工具效果仍不会回滚，精度作用域不授予额外能力。

## 复数、宏与数据

Complex 任一部件为 Decimal 时，两部件必须为 Decimal／Int，Int 精确转 Decimal。float／Fraction 需预先转换。复数加减乘的各个原语遵当前上下文；复数除法先构建有界精确有理商，再仅对最后两个部件各舍入一次，避免中间平方溢出／提早丢失精度。共轭精确反转虚部符号，保留量子。

宏 decimal／decimal_text／decimal_quantize 使用编译器独立默认 precision28 half_even 上下文。runtime context/get/info/clear API 不属于纯宏；syntax_literal 捕获真实构造器身份，调用方同名函数不能替换它。宏输出保留原始源码／IR 的权威地位。

numeric_encode(Decimal) 返回 `{"$numeric":{"kind":"Decimal","sign":0,"coefficient":"10","exponent":-2}}`；numeric_decode 校验封闭字段，显式重建值。Complex codec 可以含 Decimal 部件。parse_json 不自动解码标签；Swarm／Program／报告通过这些普通 Records 和 decimal_text String 保存。Context 是活句柄，序列化只能得到 `$decimalContext` 配置／flags 快照，不自动创建新上下文，也不能作为 Numeric 编码。

## 资源与兼容边界

公开 coefficient 至多 1024 digits，stored exponent 在 ±2048 内；precision 1..1024，Emin／Emax 在 -1024..1024 且 Emin≤0≤Emax。Etiny=Emin-precision+1。解析前限制文本长度／ASCII 格式，指数展开与宿主内核调用前进行预检和预算检查。Decimal Complex 精确商 scratch 最多 56004 bits；原 Fraction 14000-bit 结果与 28002-bit scratch 不变。宿主数学内核在这些固定规模内运行，无法在其单次 C 运算内部挂起。

floordiv 返回 Int，向负无穷取整；余数沿除数符号。这保留语言原运算规则，刻意不同于 Python Decimal 的截断除法／被除数符号余数。Decimal 的一元舍入会标准化负零，constructor／text／codec 保留负零。整数 JIT 和 Float32 GPU 后端不接受 Decimal，不静默降精度。Python 的 NaN／Infinity、完整 Decimal API、通用张量与全部 Python／Lisp 生态仍未实现，见[能力对照](PYTHON_LISP_CAPABILITIES.md)及 [Python Decimal 文档](https://docs.python.org/3.13/library/decimal.html)。
