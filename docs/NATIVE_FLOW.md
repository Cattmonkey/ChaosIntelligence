# 可恢复的原生结构控制流

原 `compile_function(fn, target)` 增加显式 `auto-flow` 与 `native-flow`。结果仍是同一个语言的一等 Function，可用于智能体工具、Team 角色和 Program。默认及原有五个目标不变。

当前内部事件传输采用 rooted-host-events/2：native 真正选择事件、读取当前调用的代际载荷 token；portable 编码相同事件包，两者均解析受验证的实际槽后才派发。调用状态保留自己的函数、作用域、结果和待决引用，结束沿原清理退役 pins。外域、旧代际、已关闭或错误 epoch 的 token 不作为指令索引回退。该局部根域不等于全语言堆、GC 或可复制资源；原公开查询字段和底层 resume 二元组不变。

```chaos example=native-flow-basic
fn sum_to(n) {
    var i = 0;
    var total = 0;
    while i < n {
        total = total + i;
        i = i + 1;
    }
    total
}
let fast = compile_function(sum_to, target="auto-flow");
[fast(4), compile_flow_info(fast).abi, length(compile_info(fast))]
```

结果为 `[6,"pc-host-events/1",12]`。安装可选 LLVM 后端后，实际机器码通过 PC switch、条件分支与跳转选择后继事件；在每个宿主事件前保存续接 PC 并返回。原 block/while/for CHECKPOINT 由主机真实让出调度器，之后从该 PC 恢复。机器栈不会跨等待存在，也不在 C 回调中执行语言生成器。

## 一个语言运行时

操作数的子表达式沿原顺序执行一次：先被调对象，再从左到右处理位置／关键字参数及展开；list／tuple／record 保持原元素／值顺序。`&&`／`||` 的原 Bool 检查和短路继续决定是否需要右侧，`|>` 继续使用原调用协议。let 与 var 的原契约检查时点不合并；do 保留原作用域规则。普通实参和容器取多结果首项，块尾、return 和真正尾调用保留原完整完成。等待、yield、错误、取消与资源退出均从当前帧继续，不重演已提交的前缀，也不增加用户检查点。

名称赋值及成员／索引的读取、写入、复合赋值也递归选择真实 CFG 子表达式。原 ClassSyntaxEvaluationFrame 保留名称赋值的右值与命名空间提交；原 ObjectAccessEvaluationFrame 保留 target、key、旧值、右值和写回阶段；原 ItemKeyFrame 依次推进标量键、slice 的非空端点和多索引组成部分。原生与便携后继都进入这些原帧，不另造访问执行器或凭事件名称推断效果。

对象、键、旧值与右值依原顺序只求值一次；复合赋值先读取旧值，再求右值，最后按原协议和契约写回。失败保留已经发生的右值效果，不提交不合法的字段值；原命名空间／描述符／`__index__`／item 协议的动态检查、同步写入约束和额外检查点不变。访问子表达式都不是尾调用，取原多结果首项；区域资格只在原先允许的名称赋值右值与复合赋值右值继承，其它 target／key／item 路径不会重新取得被禁止的区域权限。

保存的便携 artifact 仍按原精确版本合同重验源码和降低后的计划；它没有新增旧计划迁移或前端绕过保证。executable 镜像格式和无源码载入合同不变，只有用户显式调用 `compile_function` 才降低已载入 AST。标量区域的原守卫、检查点和快速路径继续有效；并非每个操作数都必须绕过已验证的区域内核。

普通、异步、生成器及异步生成器函数中，block、if、受原语法允许的循环、耗尽 else、break／continue、return、保护、资源和模式结构进入同一续接 CFG。call、let、var、list、tuple、record、binary、unary 与 do 的子表达式也递归降低：绑定右侧、被调对象、每个实参、容器元素、记录值与运算输入中的结构控制由真实 CFG 后继选择。原 OperandFrame 保存并推进实际阶段；函数、方法、实例、泛型、智能体与工具调用的准入、参数绑定、Env／Cell、类型契约和运算协议继续使用原运行时。定义、推导等未选中的宿主子树仍按原 AST 边界执行。可选的整数区域仍复用受守卫的 LLVM 内核，不要求每个可编译函数含有数值表达式。任意精度整数或用户运算对象可以沿原协议求值，包括协议函数内额外的真实 CHECKPOINT。这不是整函数所有操作都在机器码中执行。

for 来源可以是任意原语言表达式，例如 `std/iter` 的函数调用；只求值一次，随后通过原同步迭代协议取得并推进游标。每一项在新的词法环境里绑定，结构模式使用原子匹配及原外层名字查找。来源表达式中的 break/continue 仍属于原外层循环：取得来源之前，新循环尚未进入；来源、获取游标或推进中的控制信号与错误不会被误当成本轮循环体的 break/continue。

```chaos example=native-iteration-stream
// Consume a bounded prefix through the original iterator protocol.
import "std/iter" as I;
fn solve(items) {
    var total = 0;
    for value in I.take(items, 2) {
        total = total + value;
    } else {
        total = total + 1;
    }
    total
}
let fast = compile_function(solve, target="auto-flow");
fast([40, 1])

```

结果为 42。这里的 `I.take` 沿原运行时执行；循环体含有普通调用时也走相同语义入口，循环后继由编译 CFG 选择；编译不获取游标或执行回调。调用不是被替换成某几个内置名称的特殊指令。

```chaos example=native-iteration-pattern
fn sum_pairs(pairs) {
    var total = 0;
    for (left, right) in pairs {
        if left == 0 { continue } else { null };
        total = total + left + right;
    } else { total = total + 0 };
    total
}
let fast = compile_function(sum_pairs, target="auto-flow");
fast([(0, 99), (20, 1), (20, 1)])
```

结果为 42。自然耗尽执行 else；break、提前 return 和传播错误不会把未消费的来源读完。循环只借用游标，既不隐式关闭上游，也不在整个循环体期间持有新的 busy 锁；暂停后或回调中显式推进同一上游会改变下一次取得的位置。资源需要按原 `closing`／`aclosing` 合同显式管理。

```chaos example=native-iteration-borrowed
import "std/iter" as I;
fn first(items) { for value in items { return value }; 0 }
let fast = compile_function(first, target="auto-flow");
let cursor = I.count(40);
[fast(cursor), next(cursor).value]
```

结果为 `[40,41]`，没有多读一项。每个原节点 tick、成功迭代的额外 tick、源位置、作用域创建及写入顺序保持一致。主机通过既有显式捕获帧保留当前作用域、模块来源、游标、当前项和仍需使用的中间值；已经消费的语句／迭代临时根会释放，break/continue/return、错误与取消按 LIFO 退出活动帧。这同样修正原 while 控制流累积历史临时结果的问题，不是自研 GC 或任意宿主对象扫描。LLVM 只处理 PC 和控制标志，不持有用户对象地址。

## 函数作为可组合的计算单元

完整源码：[native-composition.chaos](../examples/native-composition.chaos)。可直接交给同一 SDK 或 CLI 运行。

```chaos example=native-composition-body
// Calls use the original binder and scheduler within the compiled CFG.
fn adjust(value, /, *, offset=2) { value + offset }
fn solve(values, /, *, offset=2) {
    var total = 0;
    for value in values {
        total = total + adjust(value, offset=offset);
    }
    total
}
let fast = compile_function(solve, target="auto-flow");
fast([40])

```

结果为 42。`adjust` 是真实闭包调用，使用原参数绑定器和当前权限；LLVM 选择循环控制后继，宿主执行调用。相同的普通编译函数可以作为 AgentTool、Team 角色或 `std/swarm` worker，也可以调用这些语言值并返回原结果。不存在另一套智能体调用协议；角色权限、任务局部上下文、录制、错误因果链、工具取消和所有权检查仍通过原运行时。 直接调用 AgentTool／Team 时，权限拒绝继续沿原调用异常传播；作为 Bot 回调执行时，普通执行／验证错误按原控制器合同返回 `needs_reconciliation`，保留已完成证据，不能把 `.output` 为 null 或外层没有捕获异常当作成功。调用方须检查结果的 `status`、`done`、证据和实际效果回执，再决定是否显式协调；编译不会替它重新执行失败或不确定的动作。

原参数合同完整保留：位置专用、关键字专用、默认值、`*args`、`**kwargs` 和参数结构模式由原绑定器处理。默认值在定义时求值，编译不重求值、调用钩子或深复制可变默认值。一般函数签名不受标量内核的八操作数限制；实际编译分析仍受 128 节点、64 层、有界参数／语法元数据及 1024 条控制指令的限制。

```chaos example=native-composition-binding
let callback = fn(value, /, *, offset=2) { value + offset };
fn route(value as [first]: List, /, *rest, offset=2, **options) {
    [callback(first, offset=offset), rest, options]
}
let routed = compile_function(route, target="auto-flow");
routed([40], 10, tag="ok")
```

结果为 `[42,[10],{tag:"ok"}]`。此函数体只有宿主调用／容器事件，也有真实 PC CFG；不能把零算术内核误报成未编译。`compile_optimization_info` 只报告实际标量区域，零区域时不启用算术优化；是否执行原生控制仍通过 `compile_flow_info` 检查。

保护结构实现把直接选中的 try／有序 catch／catch*／try else／finally 区域递归纳入同一续接 CFG，保留原保护帧、尾调用解析、分组错误分区及清理优先级；模式结构实现进一步选择直接模式结构；操作数子表达式中的这些结构也通过原 OperandFrame 选择真实后继；未选中的其它宿主子树保留原语义事件；资源结构实现进一步把直接选中的四种 with 形式接入原资源帧的结构后继。外层机器分支与其嵌套的原调度器可组合；宿主事件内的未知效果始终是优化屏障，不能按函数名字猜纯度或重放调用。

尾位置由原语言结构决定：块最后一项及所选 if 分支继承尾位置，return 的操作数为尾位置，条件、实参和循环体中的普通调用不是尾调用。宿主子树保留自己的清理／返回语义；真正尾调用继续走原调用器的 trampoline，非尾递归继续受原调用深度预算；默认调用／返回阶段进入执行游标，尚未迁移的协议桥仍可能触及宿主栈。

```chaos example=native-composition-cold
var started = 0;
async fn work(value) { started = started + 1; value + 2 }
let invoke = compile_function(fn(callback, value) { callback(value) }, target="auto-flow");
let pending = invoke(work, 40);
let before = started;
let answer = await pending;
[type_of(pending), before, answer, started]
```

结果为 `["Task",0,42,1]`。普通编译函数调用 async／生成器函数时只返回原冷 Task／Generator，不隐式 await、推进或关闭。auto-flow／native-flow 也接受 ordinary async fn、gen fn 与 async gen fn 的函数体，详情见下节；流仍由消费者显式逐次恢复。普通 fn 本来不允许的 await／async for 语法不会因此开放。这个实现不是把整个函数、所有回调和资源协议都变成机器码。


## 回退与稳定定义契约

`auto-flow` 后端不可用时用同一续接 IR 的便携派发器执行；`native-flow` 要求后端成功。运行期间开启调试或表达式追踪，从当前 PC 永久切换便携派发；保留原节点、Env、Cell 和已消费的检查点，不重跑函数或语句。数值不满足区域守卫时，仅当前叶子沿原协议执行。

这两个模式要求定义在一次调用中稳定。结构、参数元数据和默认值身份参与有界无钩子指纹检查；闭包 Cell 及被捕获对象的合法状态变化继续按原语言语义读取。调用之前定义已经变化时，按当前原 AST 解释。调用进入后，在结构或叶子协议 CHECKPOINT 恢复时检测到定义变化会抛 `definition_changed`，保留已提交状态、不重放效果；来源 catch 不能把已经失效的 CFG 重新激活，必要清理仍按原错误优先级执行。此限制仅属于新模式；原五个目标的 AST 采用行为不变。调用间热更新和 Program 的下一轮采用仍可正常工作。通用活栈 AST 迁移尚未实现。

## 查询与界面一致性

`compile_flow_info(fn)` 是只读的共享 NativeSpec，返回 11 字段：schema、abi、status、instructionCount、branchCount、checkpointCount、resumeCalls、nativeResumes、portableResumes、lastPC、definitionPolicy。status 区分实际原生 CFG、便携 CFG 和未编译；计数来自真实续接调用；checkpointCount 包含同步 for 及受支持异步 for 的结构检查点。lastPC 为最后续接位置，不导出临时值、源码、地址或权限。查询不编译、不执行，返回新记录。

旧 `compile_info` 12 字段和 `compile_optimization_info` 13 字段保持不变；后者只报告标量 DAG 优化，不把控制内核伪装成标量优化。源码、格式化、IR、无界面 CLI、SDK/API 与 IDE 使用同一编译函数和运行时；字段提示、高亮与参数帮助来自共享描述。

## 尚需完成

当前未提供通用活栈持久化、全机器变量状态／去优化映射、通用 AOT／跨目标 ABI、独立 GC、通用 CUDA 编译或自举。同步解释段的可组合 continuation 已实现，但活动 FlowState 仍是捕获屏障。Python／Lisp 全功能兼容不属于语言发布目标。PC 宿主事件 ABI 是可恢复控制流的一个实际实现步骤，不代表全部语言或编译器目标完成，也不构成性能提升证明。


## 默认游标与编译宿主事件

[默认执行帧](EXECUTION_FRAMES.md)使普通操作数、函数调用和返回待决状态进入每个逻辑 Runner 的同一默认游标。flow 宿主叶子与其嵌套原协议使用相同调用入口；PC 派发、CHECKPOINT、稳定定义、取消、错误位置和原生／便携切换合同保持不变。默认解释调用不借用冻结的 CFG，也不重跑已经提交的前缀。尚未迁移的协议仍是不透明桥，现有 PC IR 和新的执行帧都没有因此成为可保存／复用／组合的 continuation。


## 异步函数体与效果恢复

`auto-flow`／`native-flow` 可以编译普通 `async fn` 的结构控制流。调用仍创建同一个冷 Task，首次等待前不执行函数体；原 `await`、能力、结构化拥有关系和取消排空由原语言运行时处理。默认 `auto`／`native` 及 `auto-control`／`native-control` 不扩大范围；生成器及异步生成器本体仅由显式 flow 目标编译。

```chaos
async fn adjusted(value) {
    await sleep(0);
    if value > 0 { value + 2 } else { 0 }
}
let fast = compile_function(adjusted, target="auto-flow");
let pending = fast(40);
[type_of(pending), await pending, compile_flow_info(fast).abi]
```

结果为 `["Task",42,"pc-host-events/1"]`。这是实际异步函数体的控制 CFG，不是调用普通包装函数后仍把整个异步函数体退回原 AST。动态调用、资源协议与未直接选中的子树仍可以作为宿主语义事件；0.130 起，async fn／async fn* 外层的 async for 及模式绑定、耗尽 else 进入结构 CFG；这些子树内部没有被冒称为机器指令。机器码只选择 PC 后继，不保存 Env、用户对象或工作线程中的语言栈。

普通函数与异步函数的 flow 计划使用同一个 CFG 控制器。机器码或便携派发返回的 PC／事件实际选择下一子节点；原默认 ExecutionCursor 中的真实 EvaluationBoundary、Operand、Loop 激活保存环境、结果、当前项与资源边界，并负责执行和捕获。宿主求值／调用帧留在同一父游标，不通过额外归一化子游标增加图深度。原函数、显式 interpreter 目标与 flow 的完整函数体采用一致的逻辑激活拓扑；显式 interpreter 目标仍真实更新原解释执行计数。其他标量／区域编译入口不因本修正冒称为相同的控制内核。

每次调用保存实际 PC、flags 和待决请求元数据。新状态只作为原捕获帧的补充根，按精确 owner／epoch／字段及实际同父游标暂停身份校验；真实词法状态按原有语义路径收费，不读取 Python generator 内部帧，也不把整个 Runner／编译器计划当语言根。退出和取消由真实活跃边界按身份退休，清理不依赖可被损坏的检查用 frames 向量；已完成状态不能再次推进。

宿主事件含保守的语法效果信息。动态调用和用户协议都是未知效果与可能挂起的屏障，不按 `tool` 等名字猜纯度，不据此重排、删除或重试动作。当前事件完成后从同一个后继 PC 恢复；调试／追踪切换只能转便携派发，不重新运行前缀。入场前定义变化可退回原 AST，执行中的结构检查点与异步等待恢复沿稳定定义策略拒绝漂移，并通过当前子运算退出清理。

AgentTool／Team／Bot 回调仍调用同一个 Function，Robot 仍遵守自己的租约、单次 command 准入及不确定状态协议。取消借用 Task 不因此获得新的拥有权；外部动作一旦准入，恢复和回退不能自动重发它。软件 Adapter 的成功不构成物理目标达成证据。

`compile_info` 仍为原12字段，`compile_flow_info` 仍为原11字段；private IR／状态不是可保存或可复用的 continuation API，也不是通用异步 AOT、自研 GC 或完整 Python／Lisp 兼容。实际原生派发、便携派发与整段原 AST 入场回退应分别观测；冷／热性能需单独实测，帧数或静态机会数不能证明加速。


## 0.129 生成器与异步生成器函数体

显式 flow 目标把 `gen_fn` 与 `async_gen_fn` 的结构控制流接入既有 CFG；编译和调用本身不消费流，首次 next／anext 前不会创建活动 FlowState。yield 与 yield from 是实际续接事件，send／throw／close 仍使用原流协议及原完成值合同；异步生成器继续显式 await。委托可以多次让出值，恢复不会重复取得已消费的项目或重新准入外部动作。

流的活动状态属于真实 stream.runner，消费者 anext Task 可以是另一个逻辑执行者。PC、权限、epoch、待决请求和 pending_kind 必须与真实 Await／Yield／效果一致；新字段只是补充捕获状态，不增加第123种算法帧，不改变原词法根的检查点收费。暂停后恢复、反馈、抛入及关闭继续检查稳定定义。关闭沿原协议和错误优先级展开；GeneratorClose 与硬 GeneratorExit／ResourceLimit 不混为一种控制，硬中止不承诺执行所有用户 finally。

AgentTool／Team／Bot 只通过显式消费包装函数取得普通结果，直接返回流仍遵守原 JSON／规划器拒绝合同。Robot 软件适配器的准入与回执仍保留原一次性命令、不确定状态和借用关系；编译不会自动重试、保存或跨进程恢复效果。查询仍为 compile_info 12字段及 compile_flow_info 11字段。独立 GC、通用 AOT／GPU 编译、跨异步／资源 continuation、外语生态兼容与物理机器人达成仍未完成；真实性能必须另测。


## 0.130 异步迭代消费者的结构 CFG

在 `async fn` 与 `async fn*` 的 auto-flow／native-flow 函数体中，外层 `async for`、结构模式绑定和对应耗尽 `else` 进入同一个 PC CFG。后端实际选择取得来源、异步步进、绑定、循环体、耗尽与退出的后继；不能把外层 flow 计数增长等同于整个循环已编译。普通 fn／fn* 不因此开放 async for 语法，特殊 IR 中原本不支持的组合也不额外扩展。

来源只求值一次，`__aiter__` 仍走原同步取得协议；每次步进创建原 anext 操作 Task，并等待其实际结果后才绑定或选择后继。协议返回的 Task、消费者 Task 与流 Runner 保留各自身份、捕获根和权限，不能将它们合并成一个假等待。结构模式仍使用原匹配与整组提交；来源、取得、步进或模式中的控制转移仍交给原词法外层，只有当前循环体捕获自己的 break／continue。

循环只拥有当前 anext 操作，错误或取消沿原规则取消该操作；借用的迭代器不会因提前 break／return 被自动关闭。显式 aclosing 继续独立拥有清理。直接选中的 try／catch／finally 区域在保护结构实现中递归包含受支持循环；模式结构实现将直接模式结构接入 CFG；其它未选中的不透明宿主子树仍保留原语义边界，其内部循环不冒称为新结构 CFG。AgentTool／Team／Bot 使用同一个函数和显式消费合同；Robot 命令准入、单次效果与不确定结果不重试的规则不变。

这不增加公开内建函数、编译查询字段或算法帧数量，也不是持久 continuation、通用 AOT、独立 GC 或完整 Python／Lisp 生态完成的声明。候选实现、实际通过的测试与正式安装是分开的验收阶段；性能与硬件结论需要独立测量。

## 保护范围中的结构 CFG

普通、异步、生成器和异步生成器的 auto-flow／native-flow，共用原 TryFrame、FinallyFrame、TryCompletionFrame 与错误组处理帧。直接选中的主体、守卫、处理器、成功 else 与清理块成为同一 FlowProgram 的真实后继；便携或本机分支决定所选守卫后的处理器以及成功分支。匹配、错误组出现位置分区、原子绑定和任意用户协议仍是原宿主算法，不按函数名假定纯度。

保护体与清理中的尾调用在退出保护范围前解决，零／多返回值保持原完成协议。清理中的局部循环转移不消费外层待决 break／continue；新的普通清理错误或 return 可按原规则替换普通待决结果。ResourceLimit、ExecutionControl 和原生 GeneratorExit 保持硬中止边界，不承诺运行用户 finally；GeneratorClose 保留原软关闭语义。活动错误、cause／context、真实 Await／Yield、借用与拥有关系仍属于原逻辑 Runner。

定义或活动状态验证失败后，不再恢复损坏 PC；只沿已经拥有的原保护范围执行原合同允许的展开。已完成的主体、守卫、工具或 Robot 动作不重放；关闭与错误不会重新取得外部动作准入。此实现没有增加算法帧，compile_info 仍12字段、compile_flow_info 仍11字段；候选源码、实际测试、安装与发布是分别验收的事实。它不是保存／复用 continuation、独立 GC、通用 AOT、跨架构或完整 Python／Lisp 生态的完成声明。


## 资源范围的结构 CFG

资源结构实现让 auto-flow／native-flow 在普通、异步、生成器及异步生成器函数中，递归编译直接选中的 with、async_with、with_pattern 与 async_with_pattern。原语法适用范围不扩大：普通函数不因此接受原本非法的 async with。后端返回的真实后继选择 manager 与 body 子树；原 ContextFrame／ProtectedValueFrame 继续负责进入、绑定、完整结果、尾调用与退出。获取失败不退出，成功获取的资源只退出一次，多资源逆序退出，模式失败先沿原资源合同清理；只有原可抑制 LanguageError 允许 true 退出值抑制。return／break／continue、NonlocalTransfer 与 GeneratorClose 不被 true 吞掉；ResourceLimit／ExecutionControl／原生 GeneratorExit 保持原硬控制策略。

PromptScope 与 CancelShield 保持原词法释放、处理器范围及延迟取消优先级。TaskGroup 仍由原 GroupRun 创建独立组体 Task／ExecutionCursor，组体使用自己的实际 FlowState 与捕获根；父状态不重新绑定到子游标。清理与取消只处理原合同拥有的操作，不把借用任务或流变成新所有物。活动定义／状态损坏只按原规则展开已经取得的范围，不恢复无效 PC，不重放工具或 Robot 动作。复用原算法帧，compile_info 仍12字段、compile_flow_info 仍11字段。

这是当前编译覆盖，语言资源功能此前已经存在。源码实现、真实测试、安装与分发分别验收；具体版本仍独立验收，不承诺性能加速、物理设备达成、全机器码、跨异步／编译／资源 continuation、独立 GC、通用 AOT／跨架构或完整 Python／Lisp 生态。宿主模型／工具／Robot 准入与整个调度周期仍保留明确边界。

## 直接模式结构的 CFG

模式结构实现将直接选中的 match、let_pattern、var_pattern 与 assign_pattern 接入 auto-flow／native-flow 的同一结构 CFG，适用于四种原合法函数形式。后端实际选择被匹配值或右值、有序守卫及所选主体；守卫仍先由原运行时检查具体 Bool，false 后继续原有序匹配并释放该分支的绑定范围。PatternSyntaxFrame、PatternCloseCursor、纯匹配器与原子提交算法保持原实现，未新增算法帧或公开查询字段。

声明仍按右值、整体契约、模式及名称预检的原顺序执行；赋值先核对所有现有 Cell 与契约，经过原最后检查点后整组提交，右值效果不回滚。选中 match 主体保留原尾位置及零／多返回值，不另加保护值归约。原生关闭沿原委托完成或异常前沿推进；借用任务／流、权限、收费根、TaskGroup 独立组体游标及清理优先级保持原合同，不重放工具、模型或 Robot 动作。

操作数 lowering 扩展已有语言功能的编译覆盖；它递归选择九类操作数、名称赋值、成员／索引访问及其完整键的真实子表达式，不把动态调用、运算协议或未知宿主操作变成可重排的纯计算。原有 AST 节点／深度及指令／标志配额继续限制准入：auto-flow 可按原规则回退，native-flow 明确拒绝不支持的计划。资源、保护及模式语义事件的效果元数据保守包含原 AST 子树的效果标签；未知调用仍是屏障，不能按函数名字推断纯度或据此重排执行。

一次调用的稳定定义检查仍在既有 CHECKPOINT／Await／Yield 前沿执行，不承诺在每个宿主 Effect 完成前新增检查点。已提交状态保留；失效后只按原规则展开已拥有范围，不重新执行前缀。源码实现、实际测试、安装与分发分别验收、不声称提速、物理设备达成、全机器码、跨资源活栈持久化、独立 GC、通用 AOT／跨目标或完整 Python／Lisp 生态已完成。
