# 计时器与多源任务等待

`sleep` 和 `task_wait` 是语言调度器的原生冷函数：它们返回 Task，只有 spawn、await 或任务组启动后才参与调度。计时不占用 Agent／工具的四路宿主工作线程；解释器仍可推进其他函数。它们使用现有 AST 的普通调用与 await，不引入另一套机器人事件循环。

## 原生计时

`sleep(seconds:Number) -> Task` 等待非负、有限秒数，完成值为 null。允许小数秒，拒绝 Bool、负数、非数字和非有限值。期限在任务实际启动时计算，创建冷 Task 不提前消耗延迟。零秒提供一次调度让出机会，不代表新线程。

```chaos example=waits-sleep
let timer=sleep(0);
let before=task_info(timer);
let value=await timer;
[before.state,before.kind,value,task_info(timer).state]
```

结果为 `["created","sleep",null,"completed"]`。原生计时使用单调时钟，系统日历调整不改变剩余时间。调试暂停时语言不推进，但单调时间继续经过；已到期计时器在恢复后处理，暂停快照不会自行倒计时。现有运行预算的暂停调整保持原样。

延迟不能扩大运行的时间或步数预算，也不能屏蔽运行取消。计时超过本次执行剩余预算时，仍以原来的 ResourceLimit 或 ExecutionControl 结束。epoch 退出撤销未完成计时器，不等待其原定期限；取消本次计时不会取消其他任务。

## 等待完成状态

`task_wait(tasks:List,until:String="first",timeout:Number|Null=null) -> Task` 返回完成状态的分区：`{done:List,pending:List,timedOut:Bool}`。done 和 pending 包含原 Task 活值，不是其返回值或错误记录。必须显式 await 一个完成 Task 才取得结果或观察它的错误。

| until | 完成条件 |
| --- | --- |
| `first` | 至少一个成员进入终态 |
| `all` | 所有成员进入终态 |
| `error` | 至少一个成员以错误／取消终止，或全部成功结束 |

任务列表在创建时复制，按第一次出现的身份去重；两个分区均保留这个顺序。空列表立即得到空分区且 timedOut=false。成员必须是同一解释器、执行周期及允许录制范围的 Task。单次输入最多10000项；当前执行周期内尚未终止的所有 task_wait（含冷任务）共同最多保留10000个去重成员引用，终止或取消会释放额度；超限抛出 ResourceLimit。启动前整体预检；成员类型或归属预检失败不会留下半批已启动任务。

```chaos example=waits-all
async fn twice(x) { x*2 }
let a=twice(20);
let b=twice(21);
let status=await task_wait([b,a,b],until="all");
[map(fn(task){task_info(task).state},status.done),
 await status.done[0],await status.done[1],length(status.pending),status.timedOut]
```

结果为 `[["completed","completed"],42,40,0,false]`。first 表示等待条件，不保证 done 只有一项：调度器恢复等待者时可能有多个已完成成员。完成后的普通错误不会被 task_wait 抛出或标记为已处理；硬错误仍遵循原运行的高优先级，不会被分区包装成成功。

```chaos example=waits-error
async fn fail() { throw "boom" }
let status=await task_wait([fail()],until="error");
try { await status.done[0] } catch error { [error.code,error.value] }
```

结果为 `["user_throw","boom"]`。省略后面的 await 不等于处理了失败；原来的未观察失败规则继续适用。读取 task_info 也不处理错误。

## 超时和借用生命周期

`timeout` 从 task_wait 实际启动时计时；null 表示不设置这一次等待的期限，0 表示即时检查。条件和期限同时满足时，以完成条件为先；只有期限先到且完成条件尚未满足时才返回 timedOut=true。超时值与 sleep 相同，要求非负、有限 Number。

```chaos example=waits-timeout
let messages=channel();
let receive=channel_receive(messages);
let status=await task_wait([receive],timeout=0);
let stopped=cancel(receive);
[status.timedOut,length(status.done),length(status.pending),stopped,
 channel_info(messages).waitingReceivers]
```

结果为 `[true,0,1,true,0]`。task_wait 借用成员：超时、取消等待者或等待者所在任务组失败，都不会隐式取消这些成员。需要取消时由程序显式选择；已经提交的 Channel 消息和外部效果不能撤回。私有原生等待只拥有自己创建且尚未启动的 sleep／task_wait 操作；中断会撤销等待自身的注册，不获得成员所有权。已经启动或其他逻辑任务创建的等待仍遵循借用规则。

TaskGroup.spawn 接纳 sleep 或 task_wait 自身，不递归收养 task_wait 的借用成员。需要词法生命周期的工作应逐个通过 workers.spawn 接纳；gather 的既有成员接纳和失败收束语义保持原样。

## 按完成顺序处理智能体函数

```chaos example=waits-workers
async fn invoke(action:Function,value) {
  await sleep(0);
  action(value)
}
async with task_group() as workers {
  var pending=[workers.spawn(invoke(fn(x){x*2},20)),
               workers.spawn(invoke(fn(x){x*2},21))];
  var results=[];
  while length(pending)>0 {
    let ready=await task_wait(pending);
    for task in ready.done { results=append(results,await task) }
    pending=ready.pending
  };
  sort(results)
}
```

结果为 `[40,42]`。将 action 换成已授权 Agent、Team 角色或持久 Session，仍使用同一 Context、能力、预算和实际效果记录。普通函数决定优先结果、回退和停止策略，运行时不会识别任务名称或提示词关键词来安排角色。示例以 sort 固定展示结果，实际完成先后不承诺输入顺序。

task_wait 不是原子 Channel select。多个已启动的 receive 可以同时提交并消耗多条消息；返回 first 分区后取消其他操作，也无法撤回这些提交。只提交一个尚未执行的收发分支使用 0.33 的 [ChannelCase 与 channel_select](SELECT.md)。跨轮定时、持久邮箱和重启恢复继续使用 [Process](PROCESSES.md)；本页计时器和等待注册仅属于当前活动执行周期。

## 提示、结构与运行态

TaskInfo 的 timing 为 null 或固定字段 Record：seconds、until、timeoutSeconds、remainingSeconds；每个字段可以为 null。sleep 的 seconds 是原始延迟，wait 的 until／timeoutSeconds 是等待条件和期限；冷任务、终态任务和无期限等待的 remainingSeconds 为 null。运行中的剩余秒数不会为负。kind 分别是 sleep／wait，等待阶段是 sleeping／waiting_tasks。任务成员 ID 继续有100项上限，不暴露结果内容。

IDE 使用原生函数规范与真实表达式信息提供高亮、参数和成员提示；`task_info(timer).` 或 `(await task_wait(jobs)).` 直接取得同一分析结果。源码、IR、格式化、CLI、SDK 与 HTTP 都调用语言核心。调试器展示暂停时的实际等待条件与剩余时间，恢复后清除旧状态。在线及离线文档保持代码颜色、提示、折行和原文复制。
