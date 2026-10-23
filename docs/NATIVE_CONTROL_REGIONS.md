# 可变控制流中的原生计算区域

0.74 在同一个 compile_function(fn, target) 中增加 auto-control 与 native-control 编译配置。结果仍是原语言的一等 Function，可作为 AgentTool、Team 或群体角色的函数实现。auto、native、interpreter 的原有子集、默认行为和两参数签名保持不变。

```chaos example=native-control-basic
fn energy(values) -> Int {
    var total: Int = 0;
    for value in values {
        total = total + (value + 1) * (value + 1);
    }
    total
}
let fast = compile_function(energy, target="auto-control");
[fast([0, 1, 2]), compile_info(fast).parameterGuards, length(compile_info(fast))]
```

结果 `[14,[],12]`。auto-control 在可选 LLVM 后端不可用时解释执行；native-control 要求支持且成功分配原生内核，否则抛 compiler_error。输入不满足当前区域的整数守卫时仍按当前表达式回退。compile_info 的 12 字段和 compile_optimization_info 的 13 字段不变。

## 原语言控制与实际机器计算

可变 var、普通赋值、结构绑定/赋值、while、for/结构循环目标、耗尽 else、break、continue、return 和嵌套块沿用原 AST 与真实 Env/Cell。每次选中纯整数 RHS 或条件区域，都以当前值的新快照执行实际 LLVM 内核；不会在不同迭代或写入间共享 CSE 结果。赋值和契约检查在 RHS 完成后沿原路径提交。增强赋值只编译 RHS，原位运算协议、原目标读取和写入保持原顺序。

严格 Bool 的 &&、||、! 与相等仍是原语言控制/运算协议，只有实际选择的子表达式可进入原生区域。未选择分支不会求值。四种有序整数比较沿原 LLVM 区域实现。

for 可直接接收原语言 List/Tuple、生成器和用户同步迭代器。迭代源在编译时只做有界 AST 指纹，不调用工厂或协议，不按 range 等名字判断纯度；真实 acquire/next、逐项新作用域、模式绑定和 break 后游标位置仍由原迭代器管理。迭代项或变量不是受证明范围内的精确 Int 时，只在当前位置解释；此前状态、游标与效果不重放，后续符合守卫的区域仍可进入机器码。

此配置不在入口将所有函数参数当作机器整数，故 parameterGuards 为空；每个标量区域仍有实际范围证明和运行时守卫。Bool 不被当作 Int，任意精度整数不静默溢出。自由变量的标量读写、任意函数体调用、异步/生成器函数、用户运算和资源控制不会被推断为纯计算；只有 for 的迭代源明确保持不透明解释执行。

## 调度、证据和统一工具

循环入口与每次块体的原 CHECKPOINT 真实让出执行权；预算、取消、错误源位置、正常耗尽 else 及嵌套 break/continue/return 完整保留。每个机器区域保留 0.73 的原顺序检查点 tape，循环不是一条预先展开的 tape。调试、表达式追踪或相关 AST 替换会让本次调用后续使用原解释路径，不重跑已完成写入。

compile_optimization_info 只读取实际区域优化计划，不执行函数。nativeCalls 仅表示本次调用至少进入一个内核，不能单独证明循环体进入机器码；开发验证应同时核对循环区域参数随迭代变化和实际 LLVM。源码、IR、Program、IDE、CLI、SDK、HTTP 使用同一 Function/AST/调度器；实际原生执行需关闭逐表达式追踪，断点仍使用真实解释环境。

## 范围

机器码处理的是循环内标量表达式，循环控制与状态提交仍由原 AST 执行。本增量没有实现整个循环 CFG 的机器分支或可恢复 PC/状态 ABI；这种 ABI 必须能在原调度检查点返回、恢复并精确去优化。整函数的可恢复控制 CFG 另见[NATIVE_FLOW](NATIVE_FLOW.md)。整程序 AOT／链接优化、独立 GC、通用 CUDA 代码生成和自举是未提供的独立方向，不是使用本语言必须补齐的外语功能。减少指令不等于已经证明提速。


## 0.75 可恢复结构控制流

[可恢复的原生结构控制流](NATIVE_FLOW.md)增加显式 auto-flow/native-flow。LLVM 保存 PC 并选择结构 CFG 分支，返回宿主后执行原叶子、Env/Cell 和真实 CHECKPOINT；调试/追踪使用同一续接 IR 在当前位置切换便携派发。新模式一次调用要求稳定定义，执行中结构检查点检测到 AST 改动安全报 definition_changed；旧五个目标不变。完整 Python/Lisp、自举、通用活栈迁移、AOT/跨目标 ABI、自研 GC/CUDA 编译仍未完成。
