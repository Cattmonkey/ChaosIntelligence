# 原生函数与智能体计算内核

原生语言函数的签名、等待结果及字段提示由共享 NativeSpec 描述，当前覆盖全部 336 个全局 Native 及原生可调用资源的合同；相关 Bot 设计见[开源对照](BOT_LANGUAGE_DESIGN.md)。下面的 `compile_function` 则说明编译机器码的执行合同。描述完整不会自动赋予编译、设备或模型权限。

`compile_function` 提供可选 LLVM 后端，结果仍是一等 `Function`，使用原有参数绑定、权限、返回契约和任务调用路径。默认 auto/native 目标编译受约束整数区域；auto-control/native-control 在原控制流中选择区域；auto-flow/native-flow 则编译普通函数的外层续接 CFG，调用和受保护语义通过原调度器的宿主事件执行。它可交给 AgentTool、Team 角色或普通高阶函数；智能体不需要另一套计算接口。

这是真正生成并执行机器码的 JIT。当前实现关注执行语义的等价，不承诺提速：每个逻辑检查点仍回调运行时，小函数可能比解释执行更慢。完整 Python／Lisp 兼容、通用 JIT、自研 GC、AOT 和 GPU 编译尚未完成。0.61 的[GPU 数值 Task](GPU_COMPUTE.md)调用设备内核，与此整数机器码合同分别提供能力。

0.59 的一次本地样本测量 `x*x+y*2`（macOS arm64、Python 3.13.9、llvmlite 0.45.1，预热后 5 组、每组 1000 次调用）得到中位数：原生路径约 117.7 微秒／次，解释路径约 51.8 微秒／次；首次后端加载和编译约 19.2 毫秒，单独计时。本样本没有加速，测量包含守卫、定义指纹和逻辑检查点；它说明当前后端的语义工程状态，不代表其他程序或硬件的性能。

## 编译和调用

```chaos example=native-basic
fn score(x: Int, y: Int = 2) -> Int { x * y + 2 }
let fast = compile_function(score);
let again = compile_function(score);
[fast(20), again(y=2, x=20), type_of(fast)]
```

结果为 `[42,42,"Function"]`。原函数不被替换；`fast` 是保留原环境与调用合同的新函数值。默认参数仍在函数定义时求值，位置专用、关键字专用和返回契约仍由统一调用器处理。

| 调用 | 含义 |
|---|---|
| `compile_function(fn, target="auto")` | 尝试本机编译；后端缺失或函数不支持时返回解释执行的函数，并记录原因 |
| `compile_function(fn, target="native")` | 要求编译成功；缺失后端或不支持的函数抛语言错误。调用时输入不满足守卫仍回退解释器 |
| `compile_function(fn, target="interpreter")` | 显式返回解释执行的函数 |
| `compile_function(fn, target="auto-control")` / `native-control` | 在原可变控制流中选择标量区域；严格目标要求后端，原范围不变 |
| `compile_function(fn, target="auto-flow")` / `native-flow` | 普通、async、generator 与 async generator 函数续接 CFG，支持完整参数绑定、原语言调用和真实等待／逐项恢复；前者无后端时便携派发，后者要求实际后端 |
| `compile_info(fn)` | 返回新记录，读取编译状态和真实调用计数；不编译、不运行函数 |

该 API 接收当前 Evaluator 拥有的语言函数值；原 auto／native／auto-control／native-control 目标仍不编译异步函数本体；只有 auto-flow／native-flow 接受普通 async fn、gen_fn 与 async_gen_fn 的函数体；原标量／区域目标仍保留解释回退或严格拒绝。普通 auto-flow/native-flow 函数可以在函数体内调用它们并返回冷句柄，见[原生结构控制流](NATIVE_FLOW.md)。Agent、宿主 Native、绑定方法或伪造记录不作为直接编译输入。`native` 的编译失败以 `compiler_error` 报告；输入／所有权错误仍抛原有语言错误。

也可以作为现有函数装饰器使用：

```chaos example=native-decorator
@compile_function
fn twice(x: Int) -> Int { x * 2 }
[twice(21), compile_info(twice).requestedTarget]
```

结果为 `[42,"auto"]`。IDE 的高亮、签名帮助、字段提示和格式化使用现有语法树及原生函数描述；通过别名或模块导入调用编译函数时仍保留目标函数的参数提示。

## 当前支持的计算区域

原 auto/native 配置的同步函数可以包含不可变 `let`、`return`、`if/else` 和嵌套块；最多 8 个简单命名参数、128 个 AST 节点。控制结构继续由原解释器执行；符合证明和输入守卫的纯表达式区域进入机器码。表达式支持参数、已绑定局部整数、整数常量、一元正负、二元 `+`、`-`、`*` 和 `<`、`<=`、`>`、`>=`。比较结果恢复为真正的 Bool。函数调用、自由变量、可变赋值、用户运算协议、容器、循环、异步与生成器不在此编译子集内。相等及逻辑运算暂不在原生区域内；比较链仍沿用现有语言规则。

```chaos example=native-structured
fn magnitude(x: Int) -> Int {
    let doubled = x * 2;
    if doubled >= 0 {
        let answer = doubled + 2;
        if answer > 10 { answer } else { answer * 2 }
    } else {
        return -doubled + 2
    }
}
let compiled = compile_function(magnitude);
[compiled(20), compiled(-20), compiled(3)]
```

结果为 `[42,42,16]`。分支只执行被选中的块；内外层同名变量、早返回和返回契约均使用原语言语义。后端缺失时，同一源码自动解释执行。

编译器根据实际 AST 和绑定关系分析，不根据提示词、函数名或代码文本猜测纯度。宏已经展开；其卫生命名映射仍须保留。不能把 Agent、模型、工具、电脑操作、持久状态或任意用户方法塞进原生区域，也不会将这些效果常量折叠。

参数守卫要求运行时值是精确的整数，布尔值不作为整数。编译器从对称范围 `[-2147483647,2147483647]` 开始收窄，证明每个中间结果都在有符号 64 位范围内；无法得到安全范围则不生成内核。`compile_info(fn).parameterGuards` 给出实际范围。

```chaos example=native-big-integer
let twice = compile_function(fn(x: Int) -> Int { x * 2 });
[twice(21), twice(100000000000000000000)]
```

结果为 `[42,200000000000000000000]`。参数超出守卫时，在函数体逻辑执行之前进入原解释器；局部值超出某个区域的守卫时，仅解释当前表达式。已经完成的绑定、条件与语句不会重跑，后续符合守卫的区域仍可进入机器码。语言整数不会静默溢出成机器整数。

```chaos example=native-mixed-range
let fourth = compile_function(fn(x: Int) -> Int {
    let square = x * x;
    square * square
});
fourth(60000)
```

结果为 `12960000000000000000`。原生后端可完成第一个区域，第二个区域继续任意精度解释计算。

## 预算、取消、调试和生命周期

参数检查、函数所有权、能力收窄、调用帧和返回检查继续由原调用器处理。原生代码只处理整数，不接收 Env、工具句柄、对象指针或 API 凭据。它在原 AST 的每次逻辑 tick 处调用受控回调；回调异常立即终止机器码路径，再在 Python 边界抛出原错误。0.73 的逻辑 checkpoint tape 始终按原位置和次序完整执行；仅已证明总定义的纯数值计算改变物理顺序，在 tape 成功后计算。失败后不提交输出，没有回放外部效果。

函数体及每个被选中分支块原有的协程检查点保留，运行时真正让出执行权。上述区域目标的控制节点仍使用 `_s_evaluate/_s_execute`；原生回调不能代替协程挂起。局部变量留在真实 Env，编译上下文作为本次调用的私有参数传递，不存入共享 Evaluator 开关。开启表达式跟踪或调试时使用解释器，以保留完整事件与断点；平台内部仅用于生命周期的跟踪通道不会使普通 IDE／API 运行永远失去原生路径。原生执行仍受当前任务的预算和取消信号约束，不引入独立权限。

IDE 运行配置提供“逐表达式追踪”开关，默认开启，保持原有逐步轨迹。关闭后仍记录运行、任务、智能体与工具事件，并允许原生计算函数执行；该偏好在当前浏览器持久保存。SDK／HTTP 使用同一个 `trace` 参数。比较界面与无界面执行时须采用相同追踪设置；编译计数随选择相应变化，函数结果与权限合同保持一致。调试运行继续保留解释器断点。

区域目标在每次调用以及每个原生区域入口检查受限函数定义及源码位置的指纹；flow 目标的调用内稳定定义合同见[原生结构控制流](NATIVE_FLOW.md)。宿主修改公开 AST 或绑定元数据后，旧机器码不再作为该定义执行，调用回退当前解释器。每个 Evaluator 保留原有的32次原生内核准入额度；同一机器码的缓存命中也占用一次，结构化函数的每个区域内核分别计数。即使后续编译失败，已经成功取得的内核也计入额度。活动函数持有执行引擎，避免正在使用的机器码被释放。没有跨程序缓存闭包或权限，也不将机器码写入 Program JSON、磁盘缓存或升级包。

编译操作按需同步进行，规模有界；它不是可抢占的后台优化器。执行服务仍使用原有异步运行架构，但不能据此承诺 LLVM 编译本身零延迟。

## 有界原生编译制品复用

标量区域与 `native-flow` 的 PC／flag 控制流共用同一个进程内机器码缓存。每次仍检查实际后端、构造本次可信 LLVM IR，并执行原分析、范围证明、编译检查点和准入计费；精确命中时只省去重复的 LLVM 解析、验证及 MCJIT 引擎构建。键包含生成 IR 的完整内容、入口符号、调用 ABI、llvmlite／LLVM 版本、目标架构及数据布局，不能仅按函数名字或算式文本复用。

每次逻辑编译仍返回独立的原生包装器，保存本次优化计划、控制流边界和调用诊断。共享制品仅持有机器码及 LLVM 引擎资源，不保存 Function、AST、Env、闭包、权限、输入、输出、活 PC 或检查点回调。不同函数和智能体的调用参数、源码位置、取消信号与能力范围仍由本次调用提供；相同控制布局可以复用机器码，宿主语义叶子仍属于各自的 Program 与函数。

默认 LRU 最多保留32个成功制品及2,000,000字节生成 IR；容量统计的是保留 IR 的 UTF-8 字节，不是机器码、LLVM 引擎或整个进程的堆内存上限。超限制品继续正常编译而不保留，失败不缓存。并发构建由原编译锁串行化；清空后的旧构建不能重新填回缓存。淘汰、清空只释放缓存的引用，活动函数继续强引用其引擎；默认进程内缓存随服务退出释放。可信宿主可另行显式配置 `NativeObjectCache`，将真实标量／flow 对象存入私有认证目录，并在兼容新进程中验证后重新链接而不再发射机器码。未配置时没有磁盘存储；源码和 HTTP 不提供 native-object 信任入口。详见 [SDK](SDK.md#宿主私有原生对象缓存)。

这项复用不改变 `compile_function`、`compile_info` 和优化查询的语言签名或返回记录。冷／热路径仍有相同的语言步骤、守卫、错误位置与取消合同；缓存命中不是一次函数执行，也不是性能加速倍数。编译仍按需同步进行，后台热点编译、通用 AOT 和独立 GC 尚未实现。

## 编译证据

`compile_info` 的 `schema`、`status`、`requestedTarget`、`backend`、`target`、`compilerVersion` 描述实际选择：`status` 为 `native`、`interpreter` 或 `unsupported`，后两种仍可按原函数执行；`nodeCount` 和 `parameterGuards` 描述已分析的区域。`fallbackReason` 说明未编译原因，`lastFallbackReason` 说明最近一次回退原因。

`nativeCalls` 统计函数调用中真正进入机器码的次数，同一调用的多个区域只计一次，包括因预算或取消而中止的调用；`interpreterCalls` 统计调用中使用解释路径的次数。一个调用若既执行原生区域又发生区域守卫回退，两项都计一次，所以它们的和不能当作总调用次数。控制节点沿解释器执行本身不算回退。不能把编译成功当作已执行，也不能把计数当作性能基准。普通未编译函数没有历史计数。返回记录不暴露机器码地址、函数环境、源码或活能力。

[`program_compile`](COMPILER.md) 继续返回 `portable-ast` 的静态分析报告，不运行 `compile_function`。静态报告的源码身份与运行期原生函数的状态分别说明不同事实；IDE、CLI、HTTP 与 SDK 始终运行同一源码快照。

## 安装和平台边界

基础安装保持 Python 3.9+ 可用；缺少可选后端时 `auto` 自动使用解释器。原生额外依赖要求 Python 3.10+，当前适配 llvmlite 0.45.x：

```sh
python -m pip install 'chaos-intelligence[native]'
# 源码目录开发安装：
python -m pip install -e '.[native]'
python -m chaos run examples/native-functions.chaos
```

没有调用编译操作时，不导入或初始化 LLVM；代码分析与页面展示也不会分配可执行内存。后端检查系统是否允许 JIT，并使用当前宿主的目标三元组、数据布局和调用约定。当前原生验收平台为 macOS arm64、Python 3.13、llvmlite 0.45.1；其他 CPU／操作系统需要各自验收，不能把宿主目标选择描述成已完成跨架构编译。Python 3.9 不安装该额外依赖。

实现采用 llvmlite 的 LLVM MCJIT 引擎，不是 ORC 或 ThinLTO。引擎负责机器码生命周期，语言层负责守卫与检查点。[llvmlite 执行引擎说明](https://llvmlite.readthedocs.io/en/v0.45.1/user-guide/binding/execution-engine.html)

后续的效果 IR、独立 GC、调度性能与异构内核合同见[运行时演进设计](COMPILER_RUNTIME_DESIGN.md)。


## 0.72.0：原生任务局部上下文

[任务局部上下文与动态绑定](DYNAMIC_CONTEXTS.md)说明 ContextVar、ContextToken、ContextScope 和 Context 的实际语言语义：普通任务/生成器创建时浅复制绑定，词法与透明协议调用共享上下文，选定 async 回调原 AST 执行，退出与取消恢复绑定。上下文可保存合法原生语言值，不能扩大工具权限。源码/IR、IDE、CLI、SDK 与 HTTP 共用实现；复制是有界 O(n)，宿主 GC 未被替换。本增量不表示完整 Python/Lisp 生态或自托管已经完成。


## 0.73 原生计算优化

[整数 DAG 优化](SCALAR_OPTIMIZER.md)实际将常量折叠、CSE 和依赖调度用于已有单表达式和结构化区域的 LLVM 发射。完整原检查点与错误位置不变，旧 compile_info 的 12 字段保持原样；compile_optimization_info 提供独立只读证据。它不是整程序重排、链接优化、通用纯函数推导或性能加速结论。


## 0.74 可变控制流中的原生区域

[可变控制流中的原生计算区域](NATIVE_CONTROL_REGIONS.md)将显式 auto-control/native-control 配置接入原 compile_function 两参数接口。原 Function/AST/Cell、同步迭代器、循环耗尽 else、break/continue/return 与真实 CHECKPOINT 保持不变；选中表达式逐次读取当前值并进入受守卫的 LLVM 内核。增强赋值只编译 RHS；严格 Bool、短路与相等使用原协议。旧 auto/native/interpreter 的范围、默认行为、compile_info 12 字段和优化查询保持兼容。此实现不是整个循环 CFG 的机器分支、可恢复 PC ABI、AOT、自研 GC、完整 Python/Lisp 或自举。


## 0.75 可恢复结构控制流

[可恢复的原生结构控制流](NATIVE_FLOW.md)增加显式 auto-flow/native-flow。LLVM 保存 PC 并选择 while／同步 for（含原结构模式）的 CFG 分支；for 来源表达式及迭代协议沿原调度器执行，宿主保持当前作用域和有界活动根，消费后的语句／迭代临时根及时释放。普通函数的实际调用与完整参数绑定继续由原运行时处理；call／let／var／list／tuple／record／binary／unary／do 的真实子表达式通过原 OperandFrame 与结构 CFG 组合；名称赋值、成员／索引读写与复合赋值通过原语法／访问帧选择完整键及右值子树；直接选中的 try／catch／finally 在保护结构实现中使用原保护帧的结构后继；四种 with 形式在资源结构实现中以原资源／特殊范围帧选择结构子树；其协议与其它不透明子树保留原调度边界和尾位置，冷句柄不隐式消费；async／gen／async gen 本体仅由 auto-flow/native-flow 编译，冷句柄保持原样并由调用者显式等待或消费；调试/追踪使用同一续接 IR 在当前位置切换便携派发。新模式一次调用要求稳定定义，执行中结构检查点检测到 AST 改动安全报 definition_changed；旧五个目标不变。通用活栈迁移、通用 AOT／跨目标 ABI、独立 GC、自举和通用 CUDA 编译不在当前范围；完整 Python／Lisp 兼容不属于语言发布目标。

[多返回值](MULTIPLE_VALUES.md)的原生调用沿宿主语义事件执行；native-flow 保留完成结果，标量后端无法编译的调用仍按已有目标合同回退或报错。编译报告继续将调用视为潜在效果，不能凭原语名称重排 producer。

[控制提示与动态出口](CONTROL_PROMPTS.md)保持原调用语义与 protected 展开路径。编译前端继续把调用视为潜在效果，native-flow 宿主叶子不能重放控制或 wind 回调；标量后端无法编译时沿原目标合同回退或报错。

[原生 Robot](NATIVE_ROBOTS.md)的十个全局接口与 Robot.__call__ 使用相同 NativeSpec／Task 元信息。编译后的普通策略函数仍沿原调用与效果路径，live 观察／命令不能变成无权威 JSON 后伪造，也不能重复发送以填补不确定结果。

具名手机/设备通过 `computer_attach(name)` 接入相同的 Computer 类型，详见[移动设备与跨设备函数](MOBILE_COMPUTERS.md)。


0.130 的 auto-flow／native-flow 将 async fn／async fn* 外层的异步 for、结构模式和耗尽 else 纳入原效果感知 CFG。原 `__aiter__`、anext Task、等待、取消与借用生命周期保持不变；直接选中的保护结构在保护结构实现中递归包含受支持循环；四种 with 形式在资源结构实现中递归包含受支持子树；九类操作数子表达式中的循环也递归进入 CFG；其它不透明宿主子树内循环仍沿原语义边界。普通 fn／fn* 的语法和原标量／区域目标的限制没有扩大，详见[异步消费者 CFG](NATIVE_FLOW.md#0130-异步迭代消费者的结构-cfg)。

保护结构实现扩展保护结构的效果感知 CFG，复用原错误匹配、尾调用、多返回值、清理优先级与动态错误上下文；详见[保护范围 CFG](NATIVE_FLOW.md#保护范围中的结构-cfg)。原标量／区域目标、权限、冷句柄、信息字段与物理设备准入合同不变，实际版本测试、分发与安装分别验收。


## 资源范围的结构 CFG

资源结构实现让 auto-flow／native-flow 在普通、异步、生成器及异步生成器函数中，递归编译直接选中的 with、async_with、with_pattern 与 async_with_pattern。原语法适用范围不扩大：普通函数不因此接受原本非法的 async with。后端返回的真实后继选择 manager 与 body 子树；原 ContextFrame／ProtectedValueFrame 继续负责进入、绑定、完整结果、尾调用与退出。获取失败不退出，成功获取的资源只退出一次，多资源逆序退出，模式失败先沿原资源合同清理；只有原可抑制 LanguageError 允许 true 退出值抑制。return／break／continue、NonlocalTransfer 与 GeneratorClose 不被 true 吞掉；ResourceLimit／ExecutionControl／原生 GeneratorExit 保持原硬控制策略。

PromptScope 与 CancelShield 保持原词法释放、处理器范围及延迟取消优先级。TaskGroup 仍由原 GroupRun 创建独立组体 Task／ExecutionCursor，组体使用自己的实际 FlowState 与捕获根；父状态不重新绑定到子游标。清理与取消只处理原合同拥有的操作，不把借用任务或流变成新所有物。活动定义／状态损坏只按原规则展开已经取得的范围，不恢复无效 PC，不重放工具或 Robot 动作。复用原算法帧，compile_info 仍12字段、compile_flow_info 仍11字段。

这是当前编译覆盖，语言资源功能此前已经存在。源码实现、真实测试、安装与分发分别验收；具体版本仍独立验收，不承诺性能加速、物理设备达成、全机器码、跨异步／编译／资源 continuation、独立 GC、通用 AOT／跨架构或完整 Python／Lisp 生态。宿主模型／工具／Robot 准入与整个调度周期仍保留明确边界。

## 直接模式结构的 CFG

模式结构实现将直接选中的 match、let_pattern、var_pattern 与 assign_pattern 接入 auto-flow／native-flow 的同一结构 CFG，适用于四种原合法函数形式。后端实际选择被匹配值或右值、有序守卫及所选主体；守卫仍先由原运行时检查具体 Bool，false 后继续原有序匹配并释放该分支的绑定范围。PatternSyntaxFrame、PatternCloseCursor、纯匹配器与原子提交算法保持原实现，未新增算法帧或公开查询字段。

声明仍按右值、整体契约、模式及名称预检的原顺序执行；赋值先核对所有现有 Cell 与契约，经过原最后检查点后整组提交，右值效果不回滚。选中 match 主体保留原尾位置及零／多返回值，不另加保护值归约。原生关闭沿原委托完成或异常前沿推进；借用任务／流、权限、收费根、TaskGroup 独立组体游标及清理优先级保持原合同，不重放工具、模型或 Robot 动作。

操作数 lowering 扩展已有语言功能的编译覆盖；它递归选择九类操作数、名称赋值、成员／索引读写和 slice／多索引键的真实子表达式，不把动态调用、运算协议或未知宿主操作变成可重排的纯计算。原有 AST 节点／深度及指令／标志配额继续限制准入：auto-flow 可按原规则回退，native-flow 明确拒绝不支持的计划。资源、保护及模式语义事件的效果元数据保守包含原 AST 子树的效果标签；未知调用仍是屏障，不能按函数名字推断纯度或据此重排执行。

一次调用的稳定定义检查仍在既有 CHECKPOINT／Await／Yield 前沿执行，不承诺在每个宿主 Effect 完成前新增检查点。已提交状态保留；失效后只按原规则展开已拥有范围，不重新执行前缀。源码实现、实际测试、安装与分发分别验收、不声称提速、物理设备达成、全机器码、跨资源活栈持久化、独立 GC、通用 AOT／跨目标或完整 Python／Lisp 生态已完成。
