# 原生整数 DAG 优化

0.73.0 为已有 compile_function 的单表达式与结构化标量区域加入真正参与 LLVM 生成和机器码执行的整数优化器。它不重写源码、Program IR 或活动函数定义；普通函数仍是一等 Function，可放进 AgentTool、Team 和普通高阶组合。不是新的智能体应用层。

```chaos example=scalar-optimizer-basic
let fast=compile_function(fn(x){(x+1)*(x+1)});
[fast(6),length(compile_info(fast)),compile_optimization_info(fast).schema]
```

结果为 `[49,12,"chaos.compiled-optimization/1"]`。auto 在后端不可用时仍执行原解释器。compile_function 与 compile_info 的参数和原 12 个字段保持不变；新增 compile_optimization_info(fn) 只读取已附着内核证据，不编译、不运行函数。

## 真实计算变换

优化器消费原编译器已经按未优化表达式证明的、总定义且不会 Int64 溢出的整数 IR。变量按卫生绑定后的参数位置识别，不按名字、提示词或角色文本识别。

值 DAG 合并完全相同的计算，折叠已证明安全的整数常量，去除 +0、*1、*0 和一元 + 等精确恒等计算，再以数据依赖和到结果的加权关键路径安排物理指令。不会默认交换操作数或重新结合表达式；不会把 Float32 当精确整数。`(x+1)*(x+1)` 的实际 LLVM 从两次 add、一次 mul 降为一次 add、一次 mul。`x+(2*3)+(2*3)` 消除重复常量乘法。`(a+b)+(((c*d)*e)*f)` 的长乘法链优先于独立的短加法链，展示算术指令的真实重排，而非检查点分支移动。

原始 range proof 在任何折叠前执行，`(MAX_INT64+1)*0` 不能通过最终结果为零绕过原有溢出证明。运行参数依旧先作原范围守卫；超出范围只从当前区域回退解释器，不重放已执行语句。

## 逻辑顺序与物理顺序

每个 AST node/operation 的 checkpoint 索引与源位置组成独立的完整 tape，包括被 CSE、fold 或恒等消除的计算。Tape 按原顺序全部执行，之后才执行物理纯计算。回调失败立即退出，不能到达输出 store。参数来自本次 invoke 的私有整数快照，不在回调后重读 Env。只有已证明总定义的纯数值运算可改变物理顺序；原预算、取消阈值及错误位置不变。

函数、块、分支和 return 仍由原 AST 调度器控制，协程 CHECKPOINT 仍真实让出执行权，不由 tape 冒充。调试与表达式跟踪继续回退解释器；公共函数 AST/fingerprint/区域身份变更沿用失效守卫。Agent、ContextVar、协议调用、工具、await/yield、资源与模块初始化不进入该 DAG，不能推断为纯调用或移过效果边界。

## 优化证据

查询返回新 Record：schema/status/enabled/algorithm/kernelCount，以及 originalOperations、emittedOperations、commonSubexpressions、constantFolds、identitySimplifications、checkpointCount、scheduled 和 kernels。只有实际附着的优化内核才使 enabled 为 true；无后端、未编译或不支持函数的计数为零。多个结构化区域分别保留计划并汇总。

每个 kernel 提供图、顺序与检查点 hash 和计数，不输出 LLVM、源码、地址、参数名、私有闭包值或权限。scheduled 指 surviving 数据操作的实际顺序是否变化，不把 CSE 数量减少本身计作调度变化。CSE/fold/恒等计数描述对应源计算变换，不保证简单相加等于净减少；不可达值也可被删除。旧 compile_info 的 nativeCalls 才表明调用实际走过原生路径，查询本身不增加它。

IDE 通过共享 NativeSpecs 提供函数签名与字段提示；CLI、SDK、HTTP、Program、宏生成函数、源码/IR 与可视编辑使用同一个运行时。

## 边界

此实现是现有原生整数区域的物理优化，并不是整个 portable-ast program_compile 的指令重排、链接删除或 AOT。原 compile_program 的 not-implemented 项描述整程序重排，仍准确。整循环的可恢复控制 CFG 另由[auto-flow／native-flow](NATIVE_FLOW.md)实现；它不等于所有运算都在机器码内执行。跨过程纯度证明、通用 CUDA 生成、独立 GC、自举和外语生态兼容不属于本区域优化的能力。

减少 LLVM 算术指令不证明提速：检查点、守卫、编译开销仍存在，历史 0.59 性能样本属于旧版本。本版不作未经测量的性能或专利新颖性声明。实现、源码与检查点安全边界见[原生函数](NATIVE_FUNCTIONS.md)和[编译运行时设计](COMPILER_RUNTIME_DESIGN.md)。


## 0.74 可变控制流中的原生区域

[可变控制流中的原生计算区域](NATIVE_CONTROL_REGIONS.md)将显式 auto-control/native-control 配置接入原 compile_function 两参数接口。原 Function/AST/Cell、同步迭代器、循环耗尽 else、break/continue/return 与真实 CHECKPOINT 保持不变；选中表达式逐次读取当前值并进入受守卫的 LLVM 内核。增强赋值只编译 RHS；严格 Bool、短路与相等使用原协议。旧 auto/native/interpreter 的范围、默认行为、compile_info 12 字段和优化查询保持兼容。此实现不是整个循环 CFG 的机器分支、可恢复 PC ABI、AOT、自研 GC、完整 Python/Lisp 或自举。
