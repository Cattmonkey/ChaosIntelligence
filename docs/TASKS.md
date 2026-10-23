# 词法任务组、并发汇合与真实运行态

Task 是当前解释器／执行周期中的计算值。调用 async fn 产生冷 Task，`spawn` 或 `await` 才启动它。Agent、Team 角色和异步工具都沿用这个调度器；页面不是另一套执行系统。

## 读取任务状态

```chaos example=tasks-info-cold
async fn answer() { 42 }
let job=answer();
let before=task_info(job);
let result=await job;
let after=task_info(job);
[before.state,before.kind,before.observed,result,after.state,after.observed]
```

结果为 `["created","task",false,42,"completed",true]`。`task_info(task)` 返回新的只读意义的数据快照，不启动、等待或取消任务，不把失败标记为已经处理。修改返回的 Record 不会修改调度器；数据快照也不能还原为活 Task。

| 字段 | 含义 |
| --- | --- |
| id | 本解释器内的任务 ID |
| state | Task 生命周期，如 created、running、completed、failed、cancelled |
| phase | 当前调度阶段，如 ready、waiting、effect、joining 或终态 |
| kind | 普通 task、gather、任务组 scope 或原生 channel_send／channel_receive；来自实际创建入口 |
| channelId | 原生通信操作所属的 Channel ID；其他任务为 null |
| groupId | 显式所属任务组的 ID；未归组时为 null |
| parentTaskId | 创建时所在 Task 的 ID；模块顶层可能是 null |
| memberTaskIds | gather 输入中的任务 ID，保持输入顺序和重复引用，最多100项 |
| memberCount / membersTruncated | 实际成员条目数，以及返回的 ID 列表是否被截断 |
| cancellationRequested | 是否已收到取消请求，包括暂缓交付的请求；不等于宿主工作已经退出 |
| cancellationDeferred | 是否有被当前词法保护范围暂缓的取消或组体中断 |
| shieldDepth | 当前 Task 的活动 CancelShield 数量，子任务不继承 |
| observed | 是否已有等待／汇合操作承担结果与错误处理 |
| location | 创建时的源码位置 |

任务 ID 必须结合当前运行、嵌套 Program 的 callId 和 nodeId 理解。不同解释器都可能创建 task-1；不能仅按这个字符串合并任务。

## 完整汇合

```chaos example=tasks-gather-order
async fn twice(x) { x*2 }
let first=twice(20);
let second=twice(21);
let joined=gather([second,first,second]);
let pending=task_info(joined);
let values=await joined;
[values,pending.kind,pending.memberCount,task_info(joined).state]
```

结果为 `[[42,40,42],"gather",3,"completed"]`。结果按输入顺序排列；同一个 Task 出现多次仍只执行一次。gather 自身是冷 Task，创建它不等于已经启动成员。

启动批次之前，调度器检查全部成员的类型、解释器、执行周期和录制范围。检查失败不应留下半批已经启动或被标记处理的任务。等待不会给成员增加权限、独立预算或另一个解释器。

一个成员发生普通错误时，调度器立即请求取消剩余成员，不需要等到它在列表中的位置。汇合会等待已启动的宿主效果实际结束，随后才向外传播错误。因此，在外层 catch 已经执行时，不应还有该批次的宿主线程继续产生效果。已经发生的外部副作用不自动回滚；不合作的宿主回调仍可能延长等待时间。

```chaos example=tasks-gather-error
async fn value() { 40 }
async fn failure() { throw "boom" }
try { await gather([value(),failure()]) }
catch error { [error.code,error.value] }
```

结果为 `["user_throw","boom"]`。多个普通错误保留调度器首先观察到的失败；这不是输入顺序保证，也不承诺一次暴露所有错误，不采用 Python ExceptionGroup／except* 语法；需要收集全部作用域失败时使用下述 task_group(errors="group")。

## 取消、资源限制与宿主中断

调度器区分主动 cancel(task)、运行级取消和汇合为收束兄弟任务发出的取消。同一汇合内部的收束取消不能盖住它最初的普通错误；主动取消仍保持既有不可捕获语义，取消无关任务也不会使另一个正常任务失败。

一个 Task 若由多个 gather 共享，也会共享取消结果：其中一个汇合取消它，其他汇合会观察不可捕获的外部取消，这可能优先于其他汇合自己的普通失败。不同汇合之间不进行全局失败根因仲裁；成功共享同一任务的结果仍只执行一次。需要独立取消生命周期时应创建不同 Task。下文 TaskGroup 为显式接纳的任务建立唯一所有权；gather 本身仍是结果观察与汇合操作。

ResourceLimit、运行级 ExecutionControl 和宿主 BaseException 不会降格为普通错误记录。即使它们来自列表后项，或在兄弟任务已请求取消后才由宿主返回，也不能被前面的普通失败覆盖。语言 catch 不能把这一批任务改造成成功结果。

取消只请求合作停止。运行时等待已开始的宿主工作退出，原生清理与语言 finally／资源退出钩子遵守各自规则；不会为收尾授予更多用户代码预算。完整清理边界见[资源作用域](CONTEXTS.md)和[SDK](SDK.md)。

## 智能体函数组合

```chaos example=tasks-callable-workers
async fn apply_worker(worker:Function,value) { worker(value) }
let jobs=map(fn(value) { apply_worker(fn(x) { x+1 },value) },[40,41]);
let joined=gather(jobs);
[await joined,task_info(joined).memberCount]
```

结果为 `[[41,42],2]`。将 worker 替换成已授权 Agent 或 Team 角色时，宿主调用仍走原 Context、能力交集、共享预算和真实工具事件；不会从提示词中推测依赖关系。

Plan、Goal、Skill 录制、持久 AgentSession 和 Process 继续使用各自原有协议。等待 process_start 的 Task 只等待启动回执，不等于等待持久 Process 的所有轮次，更不会自动停止它。录制失败不能作为成功技能完成；跨录制范围不能通过 gather 偷带任务。

## IDE、事件与调试

IDE 为 task_info 提供真实签名及字段类型；源码、结构编辑、CLI、SDK 和 HTTP 仍使用同一 AST。调试暂停时，解释器线程保存最多100项的任务快照，界面显示任务身份、父任务、汇合成员和实际状态，并明确标注截断。HTTP 读取只是复制该暂停快照，不会跨线程执行用户 getter 或读取不断变化的调用栈。

program.task.* 事件来自调度器；嵌套 Program 保留 callId 命名空间。事件被截断时不能从缺少终态推断任务仍在运行。任务完成类型提示是保守分析，不是完整静态类型系统。

## 语言原生 TaskGroup

```chaos example=tasks-group-scope
async fn twice(x:Int)->Int { x*2 }
let group:TaskGroup=task_group();
let jobs=async with group as workers {
    let first=workers.spawn(twice(20));
    let second=workers.spawn(twice(21));
    [first,second]
};
[await jobs[0],await jobs[1],task_group_info(group).state,
 task_group_info(group).memberCount]
```

结果为 `[40,42,"closed",2]`。`task_group()` 创建当前解释器和执行周期的组值；`async with` 进入它，`group.spawn(task)` 显式接纳并立即调度冷任务，返回同一个 Task。退出作用域之前，组必须等待所有成员完成。无需给每个成员再写 await 才能保证生命周期；await 仍用于取得各自结果。

组体由同一解释器中的真实 `scope` Task 执行，模块顶层也适用。它与成员共享原有步数、时间、能力与录制范围。这个 Task 不是新的智能体或新解释器，也不授予模型、工具或电脑权限。普通 async fn 可调用 Agent、Team 角色、工具或语言内可调用对象，再把所产生的冷任务交给组，因此生命周期属于语言。

### 失败打断、清理与控制流

```chaos example=tasks-group-failure
async fn fail() { throw "worker failed" }
try {
    async with task_group() as workers {
        workers.spawn(fail());
        while true { null }
    }
} catch error { [error.code,error.value] }
```

结果为 `["user_throw","worker failed"]`。成员失败会打断仍在计算、等待或执行宿主效果的组体，取消其余成员并等待已开始的宿主效果实际结束，再把原始失败交给组外 catch。普通失败使用内部作用域中断，不把外层函数永久取消；组体内的普通 catch 不能把已失败的组改为成功。默认多个普通失败保留调度器首先观察的那个错误；0.81 可显式选用下面的分组策略。

普通子任务失败打断组体时，组体的 finally 与资源退出钩子按普通异常展开；退出钩子不能抑制任务组失败。已交付的公共 cancel、资源限制、运行级取消与宿主中断沿用不可捕获边界，并跳过任意用户清理代码。两种情况下，调度器都负责原生成员与宿主效果的实际收尾；后到的真正硬错误不能被普通错误覆盖。

```chaos example=tasks-group-return
async fn work() { 21 }
async fn answer() {
    async with task_group() as workers {
        let job=workers.spawn(work());
        return await job*2
    }
}
await answer()
```

结果为 `42`。正常值、return、break 与 continue 都必须等本组成员完成后才能离开作用域；期间的失败可以覆盖待返回值。组体可以消费其他生成器，不能自身向外 yield 挂起一个仍然开放的任务组。需要流式结果时，让成员写入原生 [Channel](CHANNELS.md)，由组体或其他成员消费，或在组完成后再产出值；没有隐式跨消费者的组所有权迁移。

### 所有权与组合

```chaos example=tasks-group-gather
async fn value(x) { x }
let group=task_group();
let batch=async with group as workers {
    workers.spawn(gather([value(40),value(42)]))
};
[await batch,task_group_info(group).memberCount]
```

结果为 `[[40,42],3]`。接纳冷 gather 时，运行时先原子检查它及全部成员闭包，再统一归组；collector 也计为成员。同一组重复接纳已有成员不会重复执行。组不能收养已启动的外部任务、其他组的任务、祖先任务、其他解释器或录制范围的任务。预检失败发生在任何接纳或启动之前。

组体及本组成员可以动态添加任务；组体正常结束后，尚在运行的成员仍可继续添加，退出屏障会包含它们。组处于 aborting 或 closed 时拒绝新成员。普通 spawn 不会自动将后代归组；要纳入这个生命周期，应显式调用当前组的 spawn。

等待一个外部 Task 不会转移其所有权；组取消等待者时，不应把无关外部任务一同取消。这个规则也适用于异步资源钩子返回的既有 Task：作用域中断只解除借用等待。若进入／退出协议在本次调用中创建冷 Task，运行时将其视作该协议操作的私有任务；中断时请求停止，并等待它实际结束，保留后到的硬错误。在活动任务组内，协议新建的 gather 必须只包含本次协议调用新建的任务闭包；混合借用成员的 gather 在启动前以 task_group_error 拒绝。普通 gather 和直接 await 的既有语义不变。外部 gather 若观察本组成员，仍可能观察到该成员的取消结果。已发生的外部副作用不自动回滚；无法强制杀死不合作的 Python 宿主线程。

### 状态、提示与可视化

`task_group_info(group)` 复制以下有界 Record，不启动任务、不观察错误，也不转移活句柄：id、state、ownerTaskId、bodyTaskId、parentGroupId、memberTaskIds、memberCount、membersTruncated、location。成员 ID 最多100项，组体不计成员。状态依次包含 new、active、joining、aborting、closed；失败组也在排空后进入 closed。Task 的 parentTaskId 始终表示创建者，groupId 表示明确归属，两者不能混为一谈。

组值与其 spawn 方法只在所属解释器、执行周期及录制范围内有效；不能重复进入组、修改其成员属性，或把 JSON 描述恢复为活组。TaskGroup 成为内建类型名；已有代码若以此名称定义 class/data，需要重命名。

IDE 根据绑定身份提供 task_group、task_group_info、TaskGroup.spawn 的签名、成员与完成类型提示；被用户变量遮蔽时保持保守推断。语法着色、宽度感知格式化与源码／结构互转复用原语言分析器，不以提示词或固定文本推测组关系。调试暂停快照显示真实 scope Task、组归属、父组与成员；继续运行后不会保留伪实时组卡。

## 范围与后续设计

0.30 已实现语言原生词法 TaskGroup、组体抢占与同源工具支持。0.81 已增加原生错误分组和 catch*；仍未实现跨进程活栈恢复、任意任务收养、跨 yield 的开放组或 Python/Lisp 全部并发生态。完整图灵可计算性由抽象语言核心讨论，不能等同于无限资源或全部标准库已实现；见[计算语义](SEMANTICS.md)。

## 多源完成观察与计时

新增 [sleep 与 task_wait](WAITS.md)。task_wait 借用成员，返回 done／pending 活 Task 分区及 timedOut；不同于 gather 的结果汇合和失败收束，也不同于 TaskGroup 的所有权。TaskGroup.spawn 只接纳 wait 操作自身，不收养其借用成员。TaskInfo 新增 timing，kind 增加 sleep／wait，phase 增加 sleeping／waiting_tasks。

[channel_select](SELECT.md) 只有一个原生 Task，候选不是其成员 Task。TaskGroup.spawn 接纳选择本身，失败或取消会撤销其尚未提交的候选；其他生产者和共享通道仍按自己的归属运行。task_wait 可以观察选择的完成状态，不能替代其原子仲裁。

## 词法取消屏蔽（0.49）

`with cancel_shield() as protection { ... }` 和 async with 为当前 Task 暂缓局部取消与 GroupAbort；最外层退出后继续交付。进入需要一个活动 Task，子任务不继承。`task_info` 新增 cancellationDeferred／shieldDepth，调试器读取同源快照。运行级取消、周期结束和资源预算仍立即生效；见[完整契约与可运行示例](CANCELLATION.md)。


## 收集任务组的普通失败（0.81）

```chaos example=tasks-error-group
async fn fail(){throw 42}
try{
  async with task_group(errors="group") as workers {
    workers.spawn(fail())
  }
}catch* {code:"user_throw",.._} as failures {
  map(fn(e){e.value},failures.exceptions)
}
```

结果为 `[42]`。errors 是可选的仅具名参数，取值 "first" 或 "group"，默认 "first" 保持旧行为。group 模式继续在首次失败后取消本组其余任务，并等待主体、成员和已经启动的宿主效果真正结束；它收集这期间实际发生的普通语言／Agent／工具失败。取消前尚未执行的代码不会被继续执行以制造失败，已被程序处理且未逃出任务的错误也不算组失败。

同一个异常经成员、await、gather 或主体转发只计一次；嵌套任务组保留子树。排空期间已完成效果抛出的真实普通错误，即使同时收到内部取消，也会保留。组自身诱发的取消确认不成为错误叶子；外部取消、资源限制和后到的宿主硬错误仍优先，不包装成可捕获组。仅有一个普通失败时也返回单元素错误组。

顺序是调度器实际观察顺序，不能假定等于输入顺序。`task_group_info` 的字段合同及 API v1 保持不变，错误分组发生在原退出屏障，无需另启智能体运行器。完整匹配、重抛与资源上限见[Error 和 catch*](EXCEPTIONS.md)。
