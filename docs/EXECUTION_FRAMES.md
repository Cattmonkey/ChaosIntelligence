# 默认求值游标与执行帧

普通源码、结构 IR、Code／Program、CLI、SDK 和项目运行使用同一求值器。默认求值入口现在把子表达式与函数调用交给当前逻辑执行者的执行游标；待决操作数、普通函数调用和返回阶段，以及 while／同步 for／async for（含结构模式）与循环耗尽 else，以及 try／模式 catch／catch*、try…else 和 finally、with／async with（含结构模式）的待决状态，保存在类型化执行帧中。它是运行时状态的实际迁移，不需要源码选择新的解释器模式，也没有增加语言内建函数或 IDE 语法。

[Continuation](CONTINUATIONS.md) 已为显式登记的同步解释帧提供保存和重复组合；普通 call_escape 仍只在活动动态范围内转移结果。保存后的原帧继续由同一求值游标推进，活动 Env 外壳按调用重建、既有 Cell 保持共享，后缀新声明及返回闭包使用该次命名空间。Task／stream、编译状态、资源租约和未知原生桥仍是屏障。循环帧保存当前阶段、迭代来源与句柄、逐项作用域和耗尽／中断完成；只有循环体的历史范围消费 break／continue，来源、条件、迭代步进和模式绑定中的控制转移仍向外传播。else 仅在真实耗尽时进入，沿用外层词法环境、尾位置和完整结果合同。迭代获取／步进、消费、委托、流恢复／关闭和推导式由真实迭代帧执行，异步 anext 的任务准入与取消排空仍沿原调度器边界；没有自动关闭借用游标、隐式消费冷 Task 或重放已经完成的步骤。资源体、词法 PromptScope／CancelShield／TaskGroup 及组体完成状态由真实帧驱动；资源获取与退出、生成器管理器、私有 Task 等待与排空、动态 Context.run、退出栈及录制终结也由真实资源协议帧驱动；同步租约记账、硬中止与原生设备操作继续遵守原边界。挂起表达式与错误转移的语法状态也由同一真实帧推进；提供方循环与原调度器仍负责各自生命周期。桥中的宿主生成器不是可复制、可序列化或可复用的后续计算。

## 组合式后缀的执行状态

捕获记录准确帧类型、语义字段和已登记的阶段。新的调用只重建可变控制容器与激活锚点，原 Function／Cell／值身份和定义检查保留；共享 Cell 不等同于复制整个词法状态。`ContinuationCaptureFrame`、`ContinuationComposeFrame`、结果孔与已登记分派运输沿原 ExecutionCursor 执行，不创建第二个求值器或 scheduler。原前缀不重算，显式调用后的后缀按次数执行；因此用户应把不可重复的 Agent／Robot 动作放在捕获前，并在资源退役后再捕获。

原 FunctionBuildFrame 的 Env.snapshot 合同保持：函数形成时未存在的后续名称仍是 undefined。capture 后创建的局部与闭包分别属于各次新后缀命名空间。wind entered／retired 是真实义务；before 失败仅展开已成功进入的外层，after 替换错误后后续清理观察新的 error_context。硬预算／硬取消保持原优先级，不新增用户清理。

## 操作数与函数返回

显式 auto-flow／native-flow 也复用同一个 OperandFrame：编译计划为 call、let、var、list、tuple、record、binary、unary 与 do 的子表达式保存有界路由，真实后继选择下一 Boundary，原帧继续收取结果、检查契约并执行调用或运算协议。没有第二套实参绑定器、操作数值栈或效果调度器。 名称赋值、index／subscript／set_member／set_item／aug_assign 及 ItemKey 的 slice／多索引子表达式同样接入原帧的结构路由；原 ClassSyntaxEvaluationFrame、ObjectAccessEvaluationFrame 和 ItemKeyFrame 仍拥有实际状态与提交。四种函数的原尾位置、完整多结果与冷句柄规则不变；动态协议中的等待仍由原帧拥有。默认解释执行不依赖这些编译路由。

游标保持原语义的从左到右求值顺序。调用帧保存已求值的 callee、位置实参、具名实参和下一项位置；后续实参挂起不会重算前面的实参。默认值、展开、模式绑定、返回注解、尾调用及函数组合仍由原合同检查。帧字段决定后续执行所使用的值；它们并非另建的调试描述。

```chaos example=execution-frame-order
var events=[];
fn mark(name:String,value:Int)->Int { events=append(events,name);value }
fn report(left:Int,right:Int)->Int { left*10+right }
let answer=report(mark("left",4),mark("right",2));
[answer,events]
```

结果为 `[42,["left","right"]]`。容器和普通实参继续取多结果的首项；块、return、尾调用和显式接收器继续保留完整完成。临时操作数在操作完成或展开时释放，词法 Cell 与原生值的身份仍共享；这不表示复制用户堆、撤销已发生的效果或持久化运行栈。

## 已消费执行根的退役

终态 Task、Generator 和 AsyncGenerator 在原游标关闭、帧排空、已拥有的子运输完成且调度通知已排空后，释放已消费的调用输入和 Runner 动态上下文。退役在原状态保存和调用者上下文恢复之后进行；重入驱动不把仍活动的主 Task 当作完成。字段通过重新赋值释放，不清空可能与活动调用者共享的容器。真实待决 Future、yield 后仍活动的流和未确认排空的不透明运输继续持有原根。

结果、完整 Completion、多结果、错误及其载荷、句柄身份、所有权、能力、epoch、完成顺序和组／channel 元数据继续保留。重复 await 和完成后的 next 不重放主体；冷取消／关闭也不执行主体。不为释放输入而关闭借用目标或裁剪异常 traceback，故有结果、异常或组元数据引用的值仍可存活。这是已消费引用的生命周期收束，不是独立 GC、全堆可达性证明或更大的捕获预算。Task／stream 登记、完成通知和组关系继续采用原调度器；单个 epoch 的数量限制不变，应用应按既有运行生命周期结束并释放不再使用的句柄。

## 保护范围、错误分组与清理

保护值帧在退出范围前完成待决尾调用，包括 return 携带的尾调用；try 帧保存当前错误、匹配臂、守卫作用域和先前错误上下文。catch* 帧按错误出现路径保存未处理、重抛与已选择的分区，全部守卫完成后才提交当前分区。finally 帧保存原完成值或控制转移，并由真实子帧执行清理；暂停期间这些字段和引用仍属于当前游标。

```chaos
var current=1;
fn read(){current}
fn report(){try{return read()}finally{current=2}}
[report(),current]
```

结果为 `[1,2]`：返回调用在清理改写变量之前完成。普通语言错误在清理期间保留原动态错误上下文；return、break、continue 和既有软展开按原规则执行清理。资源预算耗尽、硬执行取消与强制关闭游标只恢复运行时记账，不借此启动新的用户清理代码。try…else 只在保护体正常完成时执行。函数、Task、错误组与辅助结果保持原身份、权限和所有权校验；这仍不是可复制的控制段或重新进入已经退出的资源范围。

## 词法资源与结构作用域

with／async with 的资源表达式只求值一次。资源帧保留管理器、已捕获的退出协议、进入值、结构绑定作用域、待决结果／错误及当前阶段；退出前沿保护值帧完成尾调用。绑定失败也属于已进入资源的退出范围，只有普通语言错误可被退出协议显式抑制。获取与退出仍使用原上下文协议；生成器资源、ExitStack、Computer、Device 和 Robot 沿原租约、能力、取消与排空规则执行，不能恢复已经关闭的资源。

词法 TaskGroup 仍把组体交给原 GroupRun 调度与排空，组体的正常值、软控制转移和错误由真实 GroupBodyFrame 形成 ScopeOutcome。CancelShield 保留入场检查点、原取消来源与优先级及关闭事件；PromptScope 在进入前捕获处理函数，先退出动态范围，再处理指向自身的转移。强制关闭只进行原有记账和宿主资源退场，不新增用户退出调用。这些字段不是另建的监控快照，也不是可重新进入资源的 continuation。

```chaos
import "std/context" as C;
var events=[];
@C.contextmanager
fn* resource(){try{yield {value:41}}finally{events=append(events,"closed")}}
let answer=with resource() as {value:value}{value+1};
[answer,events]
```

结果为 `[42,["closed"]]`。资源体可以挂起，退出阶段也可以异步挂起；已完成的进入、工具调用与退出不会因继续执行而重放。call_prompt／call_escape／control_barrier／dynamic_wind 及共享回调、提示处理器也由同一游标上的真实控制帧执行。处理器在目标移除后准备全部结果，回调的权限在调用与挂起期间保持收窄，再恢复原逻辑执行者的权限；wind 保留进入、主体、退出与错误上下文的待决状态。独立的控制请求与词法 prompt_scope 共享处理器帧，没有重复的整段宿主生成器实现。其余未迁移原生协议仍沿不透明叶桥执行；这些控制帧不提供保存或重新进入已经退出范围的能力。

## Bot 的规划、执行与验证

原生 Bot 的完整生命周期也由当前逻辑执行者的真实帧驱动：回调、决策准入、TaskGroup 组体、规划／执行／验证阶段、指导排空与采用、结果证据、上下文提交、拥有流的清理以及最终退役。帧保存正在使用的 proposal、token、rounds、完整回调结果、待决错误、进入标记和关闭阶段；修改待决字段会改变后续真实执行，不是单独生成一份监控描述。原 `bot(...)`、冷 Task、宿主事件与 Process 根检查点接口保持同一合同。

回调仍通过统一 Invocation 进入普通函数、AgentTool、Team 与 Program；TaskGroup 仍负责失败收束和排空。等待回调 Task 保留现有等待协议，单个流关闭进入统一的迭代／流执行帧；整段 Bot 控制算法不再藏在宿主生成器中。阶段保存并恢复实际 Runner 的决策身份；Bot 权限和角色来源先恢复，再进行可信宿主退役，busy 在关闭成功或失败后都释放。新的表示根参与完整所有权、能力和图大小检查，不额外消耗历史语言检查点。

两轮规划／执行／验证和冷调用示例见[原生 Bot 的语言执行状态](NATIVE_BOTS.md)。途中指导仍按实际 revision 在宿主锁内准入，已开始或不确定的工作不自动重放；终态提交与关闭也沿原线性化合同执行。帧并不提供活栈持久化、重新进入退役 scope、continuation 或无限自主运行。

## Plan／Goal／Skill 执行状态

Plan 调用、宿主请求、单步 action、串行扫描、并行组体、计划执行、Goal 迭代、Skill 录制、资源收束、重放和模式存取由十二个真实 Mode 执行帧推进。它们分别保存原 action、依赖结果、扫描位置、待决 Task／GroupRun、Goal claim／checkpoint、录制 scope、已观察子任务及清理错误。连同已有执行族，算法帧共137种；运输用 ModeCloseCursor 不另计帧。普通 Function、Native 和 Agent 仍经过同一调用协调，效果交给同一调度器。

创建 run_plan、pursue、record_skill、replay_skill、save_mode 或 load_mode 的冷 Task 不执行宿主操作。串行 Plan 保留原整轮扫描次序；并行 Plan 先提交全部已终结成员，再按定义次序准入，结果键仍按定义排序。组体拥有实际独立 Task／ExecutionCursor，不把父游标重新绑定给子任务；只排空原合同拥有的任务，借用任务不被额外取消。

Goal 的 claim、pending 与 verified commit 沿原持久层 lease／revision 合同。普通失败尝试 release；硬取消、预算耗尽或原生 GeneratorExit 保留实际 pending checkpoint，由宿主 reconciliation 决定后续，不能自动释放并重放不确定动作。录制在首次执行时取得真实当前 Task 的 scope；已启动后代与资源按固定点收束后才能 seal，冷后代取消而不运行，失败录制不得发布可执行 Skill。abort 与硬终结继续保留原错误优先级，移除录制身份后不能再启动该 scope 的延迟用户清理。

每个原生叶桥的 mode_cursor 补充槽暴露实际 Mode 状态；原 pending_cursor 与收费路径不被覆盖。捕获检查继续核所有权、epoch、能力与图预算，宿主前后检查点和关闭前沿保留原行为。这是执行状态表示的迁移，不提供复制活帧、重入已退出资源、撤销工具效果或新的 continuation API。

## 情况与恢复的执行状态

Condition 请求、共享恢复回调、signal／error、出口枚举与查找、inline／case 调用和交互式 debugger 由当前逻辑执行者的真实帧驱动。处理器组、当前绑定、屏蔽集合、已发现与已展示的出口、待决参数、完整结果、转移和错误保存在执行字段中；修改待决字段会改变后续真实执行。词法 HandlerScope／RestartScope 继续使用原 ContextFrame 与一次性租约，不另建动态作用域。

处理期间暂时屏蔽当前组与当时更内层组，新安装的处理器正常可见；成功、错误、取消和强制关闭都恢复原屏蔽集合与实际 Runner 的权限。case 先展开并关闭目标范围，再调用精确出口；共享回调保留原恢复标记、异步原地执行和完整多结果。已经完成的工具效果不会被重新调用或回滚。

Debugger 帧保存原 epoch、holder、能力上限、嵌套深度、展示出口与选择，沿原检查点验证恢复范围；只有实际暂停时长补回原期限。网页和终端仍提交原 pauseId／restartId，不从描述文字推断恢复动作。拥有权、权限与捕获图边界覆盖这些真实状态；这没有提供保存活栈、跨重启恢复或可复用 continuation。详见[情况合同](CONDITIONS.md)和[调试接口](DEBUGGER.md)。


## 迭代、消费、委托与推导的执行状态

获取游标、协议描述符调用、一次 Task 等待、逐项步进与关闭、collect／acollect、函数消费者、yield from，以及同步／异步流的恢复和关闭，由同一逻辑执行者上的真实类型化帧驱动。帧保存实际 handle、待决函数与参数、item、累积结果、发送值／注入错误和恢复阶段；这些字段决定下一步执行，不是从事件文本生成的状态副本。

列表、记录、集合、同步与异步生成器推导共享显式遍历状态，各子句帧保存来源、游标、独立词法环境、键、投影与待决异步取项。生成器表达式仍在创建处只求最外来源，取得游标及执行投影延迟到实际消费；已存在的可变 Cell 共享，每一项建立新绑定。记录先检查键再求值，重复键与集合重复项仍计入原配额，yield 恢复后才增加原条目计数。

消费者仍借用输入而不隐式关闭；完整消费在上限后允许一次结束探测，探测的源效果和游标推进保留，但不调用该项的回调，any／all 短路不多取项。迭代器 busy、终态缓存、一次关闭标记、冷 Task 和一次等待规则保持不变。协议回调与流恢复收窄实际 Runner 的权限并恢复原调用者，取消、错误与强制关闭也释放原锁和记账；不会借迁移重放工具效果。

原调度器的 drive、任务调度、硬中止和整个执行周期的注册表扫描仍负责协调；扫描过程中每次实际恢复或关闭进入上述帧。这不表示整个调度器、独立 GC 或全局清理扫描本身已迁移成可复制状态。提供方循环等其它原生状态继续沿各自明确边界执行，这些异步与协议状态不在同步 continuation 的可保存范围内。详细语义见[迭代器](ITERATORS.md)与[推导式](COMPREHENSIONS.md)。

## 任务与逻辑执行者

每个 Runner／流由自己的游标执行。游标把原有 CHECKPOINT、Await、Yield 和效果请求交给同一调度器，不另建任务或重放已提交效果。async 函数调用仍先得到冷 Task；透明协议回调沿原活动逻辑上下文执行，真实子任务和生成器保留原隔离与生命周期。

```chaos example=execution-frame-cold-results
var calls=0;
async fn report(value:Int)->Int {
 calls=calls+1;await sleep(0);return_values(value+1,"frame")
}
let pending=report(41);
let before=calls;
let results=multiple_value_list(async fn(){await pending});
[before,results,multiple_value_list(pending),calls]
```

结果为 `[0,[42,"frame"],[42,"frame"],1]`。读取已经完成的句柄不会重新运行任务。清理、取消、TaskGroup、Channel 和 Robot 等原生资源继续使用原调度与准入规则；已关闭的范围、已消费的命令和旧运行身份不会重新激活。

## 调度检查点与算法对照

总燃料计数还包含调度器等待外部工作完成时的全局空闲检查点。外部准入、宿主完成时机与轮询次序不同，即使语言结果相同，总步数也可能不同；这些检查点仍承担原有的预算、超时与取消检查，不因执行帧迁移而删除或抵消。

与原算法做精确对照时，需要匹配外部准入／完成调度与 UUID 等身份来源，并完整比较结果、总步数、宿主事件、工具效果、表达式追踪和调试记录六部分。固定调度的算法对照，与真实工作线程下的调度、预算、取消及排空覆盖是不同的验证范围，不能互相替代。这里不把不同调度下的差异归结为某一次历史失败的唯一原因，也不表示其余原生协议、可保存 continuation 或整个语言已经完成。

## 代码构造、宏与工具入口

Code 构造的真实 Function、Program 模块导出、运行时宏展开、AgentTool 和 Team 函数进入同一默认游标。IDE／LSP、check、fmt 和 IR 构造保持不执行源码；内部执行帧不通过原生签名、JSON 或结构编辑公开。

```chaos example=execution-frame-code-macro
macro invoke(callable,value) { quote { unquote(callable)(unquote(value)) } }
let generated=function_from(code_from("fn(value:Int)->Int{return_values(value+1,\"code\")}"));
multiple_value_list(fn(){invoke!(generated,41)})
```

结果为 `[42,"code"]`。宏生成普通运行时调用；纯编译阶段没有运行游标或捕获活状态的新权限。

## 已有编译模式

[auto-flow／native-flow](NATIVE_FLOW.md)继续使用既有 PC、结构控制流和宿主事件合同；便携派发与原生派发进入同一语言调用入口。调试、表达式追踪、错误源位置和编译模式的稳定定义检查保留原语义，默认解释执行不会因此增加新的稳定定义限制。

flow 的普通、async、gen 与 async gen 函数由同一 CFG 控制器选择真实后继，实际 body／Operand／Loop 激活位于原默认父游标；动态求值与调用不会为了运输而进入额外归一化子游标。普通函数、显式 interpreter 目标和 flow 的完整函数体采用一致的逻辑入口；显式 interpreter 仍记录自己的真实解释调用计数。环境、结果、迭代状态及原 nullable 根槽由实际执行帧持有，PC／flags／待决请求作为补充状态按 owner、epoch 和真实暂停身份审查。退出以实际已取得边界清理，损坏检查向量不能改写这些清理义务。

这修正编译表示影响活任务捕获收费／深度的差异，不是加入空节点或抵扣检查点；既有资源界限与原效果不重放合同保留。新的元数据没有增加一个虚构算法帧，也不提供堆快照、多次恢复或物理机器人达成证明。

```chaos example=execution-frame-flow
fn report(value:Int)->Int { return_values(value+1,"flow") }
let fast=compile_function(report,target="auto-flow");
multiple_value_list(fn(){fast(41)})
```

结果为 `[42,"flow"]`。现有编译查询只报告自己的真实状态，不把执行游标计作全语言机器码编译或性能提升。

完整示例见 [execution-frames.chaos](../examples/execution-frames.chaos)。同步解释段的保存／复用、共享 Cell 与每次恢复独立容器，以及 fresh wind 转换见[Continuation](CONTINUATIONS.md)。其余原生协议、资源与跨调度状态仍不可复制。该基础不表示 call/cc、完整 Python／Lisp、通用活栈迁移、自举或 AOT 已完成。


## 资源协议与动态上下文的执行状态

资源进入和退出、生成器上下文的推进与关闭、协议回调与一层 Task 等待及取消排空，和 Context.run 的安装／恢复、退出栈登记与逆序展开、录制范围终结，使用当前逻辑执行者上的真实类型化执行帧。管理器、保存的退出函数、待决 Task、私有拥有关系、当前清理项、待决错误、完整结果及上一 Context 都是继续执行的数据。它们参与相同的所有权、权限投影、别名与捕获图预算检查；没有从事件文本另建一份模拟状态。

资源协议先保存退出绑定再执行进入。生成器管理器仍要求恰好一次 yield，使用真实注入错误的身份判断退出；已借用的 Task 不被当作私有操作取消，协议创建的 Task 沿原结构化取消与排空规则退役。退出栈的预留、忙锁、父子拥有关系、pop_all 责任转移、错误替换／抑制与录制归属保持原合同。普通函数、AgentTool、Team 和 Bot 的协议回调共用这些状态，不重复获取、工具调用或已完成清理。

同步租约记账、原生 Computer／Device／Robot 操作及硬中止保留原必要边界；调度器仍协调任务、流和整个执行周期的清理扫描。真实帧不代表已经提供可复制 continuation、wind 再进入、关闭资源的恢复、自研 GC 或一般性活栈迁移。详细语言合同见[资源作用域](CONTEXTS.md)、[动态资源栈](RESOURCE_STACKS.md)与[任务局部上下文](DYNAMIC_CONTEXTS.md)。

## 对象运算的执行状态

operators 的 invoke／compare_hooks／equal／binary／inplace／operation 六段完整协作算法由真实 OperatorInvokeFrame、OperatorCompareFrame、OperatorEqualFrame、OperatorBinaryFrame、OperatorInplaceFrame 和 OperatorOperationFrame 执行。接收者、真实方法、参数、候选、相等遍历栈、待决值和阶段参与原所有权／能力／捕获图边界；普通回调继续通过原 Invocation 和效果调度执行，不重放已完成调用。NotImplemented 回退、左序相等、冷 Task、具体 Bool／Int 检查及原检查点保持不变。

提供方循环等其它原生状态仍保留明确边界；原调度器继续负责任务和全周期协调。这不是整套语言完成、活栈持久化、continuation、自研 GC 或通用 AOT 的声明。示例和完整运算合同见[运算协议](OPERATORS.md#h-运算协议的执行状态)。

## 对象属性、描述符与索引的执行状态

普通属性钩子、读取／写入／目录、描述符调用解析、原始成员委托、条目键与历史索引、无损整数及条目协议，以及属性／索引／增强赋值的表达式求值，现统一由15种真实对象访问帧推进。目标、名称、键、原值、右值、待决回调、能力、错误上下文和目录游标都是决定后续执行的数据；源码、IR、Code／Program、宏、AgentTool／Team／Bot、CLI／SDK／HTTP 与既有编译回退共用实现。原有求值次序、冷句柄、同步写入、检查点、权限及取消合同不变。提供方循环等其它原生状态仍保留明确边界；这不是保存／复用 continuation、自研 GC、通用 AOT、自举或完整 Python／Lisp 生态已经完成的声明。

| 真实帧 | 完整职责 |
| --- | --- |
| AttributeHookFrame／AttributeReadFrame／AttributeWriteFrame／AttributeDirectoryFrame | 原始钩子绑定、普通读取与一次缺失回退、同步写入／删除、同步目录消费与借用锁清理 |
| AccessResultFrame | 递归描述符解析、实际回调、深度限制与同步写入结果检查 |
| ScopedGetMemberFrame／ScopedSetMemberFrame／ScopedDeleteMemberFrame | 默认成员及原生类型分派，保持原始权限和只读边界 |
| ItemKeyFrame／LegacyIndexFrame | 左序切片／复合键求值与转换，旧方言和显式成员索引 |
| IntegerIndexFrame／IndexResolveFrame／ItemInvokeFrame／ItemOperationFrame | 精确Int转换、Slice端点、用户／元类条目分派、读写删合同 |
| ObjectAccessEvaluationFrame | 属性／索引读写和增强赋值的目标、键、旧值、右值、运算与写回 |

原始对象元信息查找与数据验证保持纯辅助计算；类创建和元类管线使用下文的21种执行帧。新帧不是在宿主生成器之外复制一份监控状态；待决字段直接用于后续调用与写回。范围退出恢复权限、原动态错误上下文与目录忙碌锁，已经发生的外部效果不回滚。实例／元类五属性钩子、描述符优先级、同步写入必须返回null、冷读取句柄及两种历史方言保持原合同。详见[属性协议](ATTRIBUTE_PROTOCOL.md#对象访问的执行状态)、[索引](INDEXING.md)和[无损整数](INDEX_PROTOCOL.md)。

## 持久哈希容器的执行状态

HashMap／Set 的15段协作算法和集合字面量求值，统一由16种真实类型化帧驱动。公开函数只转交请求；算法的草稿、循环位置、递归分支、待决调用与恢复状态属于帧本身，不存放在整个旧算法的宿主生成器中。它们与既有对象访问、相等运算和迭代帧共用调用入口，源码、IR、Code／Program、宏以及 AgentTool／Team／Bot 都沿同一实现执行。

| 帧 | 真实待决状态 |
| --- | --- |
| HashKeyFrame／InstanceHashFrame／HashValueFrame | 共享哈希预算、递归深度、活动路径与缓存、MRO 选择、协议函数和返回值 |
| HashFindFrame／HashStoreFrame／HashBuilderAddFrame | 插入哈希、碰撞桶位置、投影键、待决相等调用与持久更新草稿 |
| HashConstructFrame／HashEqualFrame／HashResolveFrame | 输入游标与忙锁、已消费数量、行位置、映射权限和操作阶段 |
| SetBuilderAddFrame／SetMappingFrame／SetContainsFrame | 累计输入次数、永久 items 根、纯值到协议表的提升、查询键和回调 |
| SetAlgebraFrame／SetUpdateFrame／SetResolveFrame | 左右表、实际集合操作、当前行、结果行与权限恢复 |
| SetLiteralEvaluationFrame | 实际表达式位置、求值环境、展开来源和同一构造草稿 |

相等键更新保留首个键对象与原插入哈希；查询、映射相等和集合代数遵守原投影与调用顺序。哈希协议必须同步返回精确 Int，相等协议仍受原同步合同约束，不隐式消费冷 Task。输入次数包含重复项；借用游标退出时仅释放自己的忙锁，不新增隐式关闭。异常、取消与强制关闭沿原权限和清理边界展开。帧根和私有草稿的显式捕获不通过读取 Python generator frame 或 closure 推断。

纯 make／items／project、portable／account 与纯键预算计算仍是同步叶操作。提供方循环与调度器仍有尚未迁移的内部状态；没有把这些容器协议扩展为通用 continuation 或活栈持久化、独立 GC、通用 AOT、自举或完整外语生态。

## 类、元类、准备命名空间与装饰器的执行状态

类声明、运行期 new_class／Type 构造、基类适配、准备命名空间的读写、元类调用与类型判断，以及函数／类／Agent 装饰器，现在由同一执行游标上的21种真实类型化帧推进。待决字段保存实际命名空间、共享 Cell、成员快照、参数、类壳和构建栈租约，直接决定下一步执行；源码、IR、Code／Program、宏和智能体回调共用这些状态。原有从左到右求值、协议调用顺序、权限上限、检查点、冷 Task、错误和取消合同保持不变。函数形成与泛型方法组合也已使用[共享执行帧](EXECUTION_FRAMES.md#函数形成与泛型方法组合的执行状态)；这没有提供保存或复用 continuation、独立 GC、通用 AOT、自举或完整 Python／Lisp 生态。

基类钩子接收实际原始 Tuple，全部基类预检先于适配效果；命名通知使用进入通知阶段时的有序成员快照。准备 Record 仍按值复制，原生映射仍保持身份，读取、写入和写后读回都使用当前映射。源类词法引用与方法闭包共享原 Cell，元类完成后更新同一引用；可变成员的真实 Cell 不被另建的状态副本替代。Type.__new__ 只复用匹配且尚未使用的当前类壳。

成功、错误、硬预算、取消及强制关闭都恢复实际进入者的权限并仅弹出自身拥有的构建栈条目；已有外部效果不会重放或回滚。所有权和捕获图预算覆盖待决状态、准备环境和选定的构建载荷，其他宿主对象保持不透明。此迁移不增加新的语言语法、隐式等待、类自动演化或可复制运行栈。

同一次捕获验证先完成原收费图，再将已完成节点及其权限上限用于补充根验证。相同或更宽权限的路径复用已完成的后代检查；更窄或交叉权限路径取交集并重新检查后代。当前节点的所有权、epoch 和结构合同仍先于复用判断检查，新出现的补充根和准备命名空间也必须完整验证。原收费图的每64节点检查点保持不变，整个图仍受20000节点和100层深度限制；复用不会跳过真正新增的状态或提高预算。

只有全部验证通过后，才提交共享权限 token 及其别名的权限交集；失败不留下部分收窄。每次捕获验证都重新建立状态，前次成功不能替代数据修改后的再次检查。这不是对遍历期间任意宿主修改提供的快照隔离，也不允许将验证状态跨解释器或执行 epoch 复用。


## 函数形成与泛型方法组合的执行状态

函数形成与泛型方法组合现在由3种真实执行帧统一推进：四种函数字面量、def／rec／agent 声明的默认值和发布阶段，function_from 的环境与函数求值，以及泛型 before／primary／after 的待决调用和范围。默认值仍在定义时从左到右求值，rec 保留原共享快照、两轮绑定及失败时已发生的行为；泛型纯选择、primary next 与尾调用辅助保持原实现，冷 Task／Generator 不隐式等待。这不是自定义 CLOS 方法组合、跨资源活栈持久化、独立 GC、通用 AOT 或完整 Python／Lisp 生态的完成声明。

| 帧 | 决定下一步的真实状态 |
| --- | --- |
| CallableSyntaxFrame | 四种函数的参数合同、默认值、参数位置，def／rec 的函数与共享环境、Agent 配置 |
| FunctionBuildFrame | 原 Code 与显式 bindings、新环境、独立 AST 和待决函数求值 |
| GenericResolveFrame | 原请求、持久方法组合、当前方法与位置、primary 完整结果、范围与权限 |

这3种算法帧与此前111种算法帧组成114种；下节增加的模式帧使现有总数达到115种。`GenericCloseCursor` 是只供泛型协议使用的精确类型子游标，属于传输与清理状态，不另计为算法帧；它不改变其他执行游标的关闭机制。正常运行时，子游标把原始 Invocation／Evaluation 等协议请求传回外层，仍由原父游标建立并执行对应的调用、求值和返回帧。父协议桥保留原有收费根，再补充子游标链接，因此新的传输表示不替代原收费路径。

公开泛型生成器自身转发 send／throw 与关闭请求。若真实宿主范围抑制了 GeneratorExit，且继续运行到下一次协议 yield，第一次 close 仍由原生生成器报告忽略 GeneratorExit；已经进入的范围、算法帧和冷协议桥保持可达，下一方法体不会因此被提前执行。再次 close 或随后 send／throw 会进入同一待决状态，按原范围退出与抑制次序继续，不重放已完成的回调。未抑制的 GeneratorExit 保留原错误身份，委托关闭失败也以真实错误进入范围。

泛型传输层区分驱动器的正常完成与算法逸出的异常：关闭阶段以显式 Done 状态表示完成，不把清理中的 StopIteration 当作正常返回。原范围仍先获得按合同抑制异常的机会；泛型算法边界之外仍未处理的 StopIteration 按原生成器规则成为 RuntimeError，原异常保留为 cause。判断依据是异常类型与完成状态，不匹配错误消息；这不表示已经保证任意宿主扩展的全面兼容。

父桥与子游标的双向链接保留到真实完成；即使外层关闭已报告错误，仍可达的待决清理状态也参与补充根验证。只接纳该精确子游标类型，并检查实际所有者、epoch、状态结构、待决请求与权限；不通过宿主生成器反射恢复状态。这是当前活跃泛型调用的协议与清理合同，不提供堆快照、运行栈复制、跨重启恢复或完整 Python／Lisp 兼容性。

公开入口只转交请求；帧不包装整段旧算法或保留无用的生成源码元数据。生成源码登记仍按原次序执行。字段中的真实值决定后继，原所有权、epoch、能力、64项检查点、20000节点与深度100捕获边界保留。before／after 同步 null 检查和范围抑制保持原语义；primary next 只进入捕获的剩余主方法，不重复辅助组合。提供方循环及原调度仍有未迁移状态；同步解释段的保存／组合及 fresh wind 已由显式 continuation 帧实现；跨异步、编译段与外部资源的再进入仍受屏障限制。示例见[callable_frames.chaos](../examples/callable_frames.chaos)。

## 模式分支与结构绑定的执行状态

`match`、`let_pattern`、`var_pattern` 和 `assign_pattern` 的完整待决算法由一个真实 `PatternSyntaxFrame` 推进，现有算法帧总数为115。它保存右值或被匹配值、分支列表与位置、当前模式、守卫、主体、绑定和词法范围；这些字段决定真正的下一步计算。被匹配值只求值一次；失败守卫的既有效果保留，后续分支得到新的绑定范围。守卫仍要求具体 Bool，选中主体保留原尾位置、完整多结果及 return／break／continue 的传播边界。

结构声明先完成右值，再按原顺序检查整体契约、纯模式及名称冲突；可变叶子仍为独立 Any Cell。结构赋值先匹配并检查所有目标 Cell 与契约，经过原最终检查点才一次提交；右值效果不回滚。纯匹配、构造器解析、类存储字段投影及原子提交仍是原同步叶算法，不新增虚假的异步阶段，不调用用户 getter、相等或迭代协议。参数、循环、推导、资源及 catch 继续共享这些叶算法。

`PatternCloseCursor` 是模式协议专用的精确类型传输游标，不另计为第116种算法帧。原生生成器负责判定子委托关闭究竟是异常还是完成；真实完成值继续进入原模式算法，因此右值完成后仍可能进行原子绑定，守卫和主体也继续遵守原类型及选择次序。忽略 GeneratorExit 时保留实际待决前沿，随后 close／send／throw 进入同一状态；不按 Python 版本或异常消息推断结果。正常 Invocation／Evaluation 请求仍交给原父游标处理并保留收费路径；新增子游标链接与待决值接受原所有权、epoch 和能力验证。

源码／IR、Code／Program、宏、AgentTool／Team／Bot、CLI／SDK／HTTP 与已有编译回退共用同一实现。真实挂起不会重放右值、守卫或主体；关闭保留原任务、借用资源及错误边界。原所有权、epoch、能力、捕获节点／深度及64项收费检查点保持原合同。提供方循环与原调度协调、跨异步／编译／资源 continuation、独立 GC、通用 AOT、自举与完整 Python／Lisp 生态仍未完成。见[可运行示例](../examples/pattern_frames.chaos)。

## 模块初始化、导入与导出入口的执行状态

封闭 `module_from` 实例的 load、imported、construct，普通项目的同步模块加载，import／export 语法，以及真实导出函数调用入口，共由六种模块算法帧保存实际后继状态；算法帧总数为122。待决模块、当前语句、词法环境、值、导出记录和初始化阶段决定后续求值与缓存提交，而不是包装旧整段生成器。公开原生生成器保留冷启动、原生关闭与再次恢复的边界；共享 NativeAlgorithmCursor 和其 ModuleCloseCursor 运输子游标，不另计语言算法帧。

两种加载方式保留原差别。封闭实例拥有自己的 loading、依赖缓存和模块调试帧；只有当前模块全部语句解析完成并读取导出后才提交该模块缓存。失败、取消或关闭清理当前 loading 与调试帧，已完成依赖及已经发生的效果不回滚，也不自动重试。普通项目加载仍同步逐语句调用原 evaluate、resolve 两个真实 Runner，不因新状态表示合并调度身份；模块结果与缓存仍在成功时一起提交。真实 Process 导出入口沿同一 ModuleExportFrame 调用并仅按原规则等待返回的 Task。

ProgramBundle、Scope 的源图与注册准备、bindings 校验、编译缓存、卫生宏和 IDE 分析保持原同步纯准备。它们不借用运行期 Env、结果、权限或模块缓存；全图、孤立文件和生成源码配额仍在初始化效果前验证。新增活根受原所有权、epoch、权限投影与原子失败、20000节点／深度100和64项收费合同约束。源码／IR、Code／Program、宏、AgentTool／Team／Bot、Process、SDK／HTTP／CLI 共用公开入口；作者工具仍不执行初始化。提供方循环、整个调度协调、跨异步／资源的 continuation、独立 GC、通用 AOT、自举与完整 Python／Lisp 生态仍未完成。见[模块示例](../examples/module_frames.chaos)。

## 挂起表达式与错误转移的执行状态

`await`、`yield`、`yield_from`、`throw` 与无操作数的重抛分支共用一个真实 `SuspensionSyntaxFrame`，该帧沿共享算法游标推进。它保存原语法、词法环境、当前阶段、已求值操作数、epoch 和能力上限；这些值决定下一步等待、委托或错误传播。`SuspensionCloseCursor` 是现有 `NativeAlgorithmCursor` 的运输子游标，不另计算法帧，也不创建另一套 Runner 或调度器。

首推进保留原局部导入、`capture_active` 与一次检查点的次序。操作数仍经原求值入口求值一次；await 继续区分当前父任务创建的冷资源／Agent／计算操作与普通等待，Robot 仍使用原 Await 和宿主准入。yield 的 send／throw 结果与同步／异步 yield from 委托继续沿已有迭代和流协议；语法帧本身不新增取消借用任务、关闭借用游标或重放效果的策略。

throw 仍按真实 Error 身份传播，普通抛出值保留原复制与异常类规则；重抛读取当前活动错误并保留同一对象。公开原生生成器保留冷启动、原生子委托关闭完成和被拒绝 GeneratorExit 后的再次 close／send／throw 前沿。关闭只沿原生前沿推进，不额外重放已完成回调或效果、再次发送机器人命令或恢复已经退役的资源。

源码／IR、Code／Program、宏、AgentTool／Team／Bot、Process、SDK／HTTP／CLI 与既有编译回退共用原入口。原所有权、权限交集、任务归属、错误优先级、捕获图20000节点／深度100和64项收费检查点保持原合同。块、列表／记录、标量绑定、if／do 等默认求值本已由 OperandFrame 接管，不能仅因历史 `_s_execute` 仍保留兼容分支就把它们列为未迁移。

提供方循环、Robot 宿主准入和整个调度周期仍有各自的原生协调边界。这一步没有提供保存／复用／组合 continuation、wind 再进入、独立 GC、通用 JIT／AOT、自举、完整 Python／Lisp 生态或物理机器人验收。


生成器 flow 的补充状态属于实际流 Runner 的原父游标，消费者 Task 不替代其所有权。生成器与异步生成器重用既有 Yield／Await／委托及关闭算法帧；flow 的 pending_kind 仅描述真实待决请求，不新增公开查询字段。关闭沿原生前沿推进，不额外重放已完成回调或效果；这没有增加保存或复用 continuation 的公开接口。


### 编译异步循环与原执行状态

0.130 的 async fn／async fn* 结构 CFG 直接使用原 LoopFrame 和 LoopCompletionFrame：来源、取得、真实 anext 操作、循环体与耗尽 else 的待决状态仍位于同一个父执行游标。没有增加第二个调度器、镜像帧或第123种算法帧。实际等待请求指向循环持有的 anext Task；自定义 __anext__ 返回的内层 Task 仍是独立的原迭代协议等待。取消、词法转移、原子模式绑定与显式资源拥有规则继续由原运行时执行。

### 保护帧的编译后继

效果感知 flow 在直接选中的 try／有序 catch／catch*／try else／finally 中复用原保护帧和 ProtectedValueFrame；主体、守卫、处理器、成功分支和清理由同一真实父游标推进。CFG 分支决定下一实际子树，原匹配、错误组分区、尾调用解析、动态错误上下文和完成优先级继续生效。没有增加第123种算法帧或另一套调度器。

活动检查失败只允许已经拥有的原保护范围按合同展开，不恢复损坏 PC。元数据容器不替代独立的实际拥有账本；退出清空实际激活、待决请求与转移根。ResourceLimit／ExecutionControl／原生 GeneratorExit 与普通错误、return／break／continue 和 GeneratorClose 保持原清理差别；借用任务或流不因表示变化取得新的取消／关闭义务。这些执行状态不是活栈保存、前缀重放、通用 continuation、独立 GC 或 AOT 的完成声明。


## 资源范围的结构 CFG

现有实现让 auto-flow／native-flow 在普通、异步、生成器及异步生成器函数中，递归编译直接选中的 with、async_with、with_pattern 与 async_with_pattern。原语法适用范围不扩大：普通函数不因此接受原本非法的 async with。后端返回的真实后继选择 manager 与 body 子树；原 ContextFrame／ProtectedValueFrame 继续负责进入、绑定、完整结果、尾调用与退出。获取失败不退出，成功获取的资源只退出一次，多资源逆序退出，模式失败先沿原资源合同清理；只有原可抑制 LanguageError 允许 true 退出值抑制。return／break／continue、NonlocalTransfer 与 GeneratorClose 不被 true 吞掉；ResourceLimit／ExecutionControl／原生 GeneratorExit 保持原硬控制策略。

PromptScope 与 CancelShield 保持原词法释放、处理器范围及延迟取消优先级。TaskGroup 仍由原 GroupRun 创建独立组体 Task／ExecutionCursor，组体使用自己的实际 FlowState 与捕获根；父状态不重新绑定到子游标。清理与取消只处理原合同拥有的操作，不把借用任务或流变成新所有物。活动定义／状态损坏只按原规则展开已经取得的范围，不恢复无效 PC，不重放工具或 Robot 动作。compile_info 仍12字段、compile_flow_info 仍11字段。

这是编译覆盖，语言资源功能此前已经存在；不承诺性能加速、物理设备达成、全机器码、跨异步／编译／资源 continuation、独立 GC、通用 AOT／跨架构或完整 Python／Lisp 生态。宿主模型／工具／Robot 准入与整个调度周期仍保留明确边界。

## 直接模式结构的 CFG

现有实现将直接选中的 match、let_pattern、var_pattern 与 assign_pattern 接入 auto-flow／native-flow 的同一结构 CFG，适用于四种原合法函数形式。后端实际选择被匹配值或右值、有序守卫及所选主体；守卫仍先由原运行时检查具体 Bool，false 后继续原有序匹配并释放该分支的绑定范围。PatternSyntaxFrame、PatternCloseCursor、纯匹配器与原子提交算法保持原实现，未新增算法帧或公开查询字段。

声明仍按右值、整体契约、模式及名称预检的原顺序执行；赋值先核对所有现有 Cell 与契约，经过原最后检查点后整组提交，右值效果不回滚。选中 match 主体保留原尾位置及零／多返回值，不另加保护值归约。原生关闭沿原委托完成或异常前沿推进；借用任务／流、权限、收费根、TaskGroup 独立组体游标及清理优先级保持原合同，不重放工具、模型或 Robot 动作。

操作数 lowering 扩展已有语言功能的编译覆盖；它递归选择九类操作数、名称赋值、成员／索引读写与完整键的真实子表达式，不把动态调用、运算协议或未知宿主操作变成可重排的纯计算。原有 AST 节点／深度及指令／标志配额继续限制准入：auto-flow 可按原规则回退，native-flow 明确拒绝不支持的计划。资源、保护及模式语义事件的效果元数据保守包含原 AST 子树的效果标签；未知调用仍是屏障，不能按函数名字推断纯度或据此重排执行。

一次调用的稳定定义检查仍在既有 CHECKPOINT／Await／Yield 前沿执行，不承诺在每个宿主 Effect 完成前新增检查点。已提交状态保留；失效后只按原规则展开已拥有范围，不重新执行前缀。本节不声称提速、物理设备达成、全机器码、跨资源活栈持久化、独立 GC、通用 AOT／跨目标或完整 Python／Lisp 生态已完成。

## 真实 receiver／producer 状态

五种 receiver 操作与两种 producer 来源由同一逻辑执行者上的两个真实算法帧推进：MultipleValueResolveFrame 保存操作、producer 顺序、当前索引、完整结果与参数草稿；MultipleValueReceiveFrame 进入原 force／context／all-results 调用，或读取原已完成 Task／Generator／AsyncGenerator 的终值并按原能力投影。公开入口不再保存整段 receiver／producer 生成器算法；MultipleValuesCloseCursor 只运输原关闭前沿，不另建 Runner 或重复效果。

零／null／多结果、普通首项投影、左到右求值、参数预算、次项所有权、call 的原 TailCall 与 bind 的原强制上下文调用保持不变。普通回调返回冷句柄仍是一个值；未完成句柄仍被拒绝，不隐式推进。纯宏使用原纯阶段合同，不借此获得运行期帧或权限。源码／IR、Code／Program、显式编译调用与 portable artifact 加载、AgentTool／Team／Bot、软件 Robot 和 SDK／CLI／HTTP 共用原求值器。本族包含两个真实算法帧，公开原语仍333个。

这是内部待决状态实现，不是通用 continuation、活栈持久化或提速承诺；源码、实测与安装分别验收。后续控制段、wind 再进入与线性屏障的具体合同见[continuation 设计](CONTROL_PROMPTS.md#continuation-design)。

## 统一调用协调状态

`InvocationDispatchFrame` 承接原调用分派算法，明确保存可调用对象查找、类调用、原生请求处理和 Agent 结果等待的阶段。原生结果按固定的类型协议顺序处理一次，32 个处理阶段的下一位置以及 Program／索引结果已校验标记都保存在帧上，挂起后继续处理，不重新调用函数或重放先前效果。普通 Function、Native、Agent、AgentTool、Team、Bot、Robot、Device、Computer 仍从同一个 InvocationFrame 进入；参数、返回契约、错误观察、调用范围释放和尾调用归约仍各自只有原来的一个实现。

`InvocationCloseCursor` 复用共享的原生协议关闭引擎。原来直接 yield 的 Agent／Native Effect 保持直接请求，避免多加一层宿主生成器改变 StopIteration 的异常含义；原 yield-from 叶子保留自己的委托边界。调用协调状态通过独立的补充观察槽接入捕获图，原算法的 pending_cursor、收费根与遍历路径不被占用。关闭或完成后仅释放本次协调状态的观察槽。

公开原语仍 333 个。本项补齐实际调用协调状态，没有增加保存或复制活栈的 API。资源、工具请求和机器人命令仍遵循原所有权及一次准入规则；续延的控制段复制、线性资源屏障与 dynamic-wind 重入仍需实现。测试、源码采用、服务安装与包分发各自保留运行证据。
