# 原子通道选择

多角色智能体常要等待工作结果、停止信号或期限。`channel_select` 是 AST 调度器的原生冷 Task：在多个候选收发中只提交一个分支，其余分支既不发送也不消费消息。它不调用模型，不占用工具效果线程，也不启动多个 Task 后再取消败者。

## 候选是一等语言值

`receive_case(channel:Channel) -> ChannelCase` 描述接收；`send_case(channel:Channel,value:Any) -> ChannelCase` 描述发送。构造候选不入通道队列、不发送或接收。候选可以保存在 List／Record、通过函数返回、作为合规 Channel 消息传递，并重复用于多个选择。它不是 Task，不能直接 spawn／await。

候选不可修改。发送候选创建时复制 List／Record 容器；每次 channel_select 再捕获独立副本，因此一次选择的接收结果不会改写该候选的下一次发送。函数、对象、Task 等合法活值仍保留身份，所有权会重新验证，不承诺任意对象图的深度冻结。`case_info(case:ChannelCase)` 仅返回 id、op、channelId、location；调试和宿主描述都不展开发送正文。

```chaos example=select-receive
let a=channel(1);
let b=channel(1);
await channel_send(a,40);
await channel_send(b,42);
let chosen=await channel_select([receive_case(a),receive_case(b)]);
[chosen.index,chosen.op,chosen.done,chosen.value,chosen.timedOut,
 (await channel_receive(b)).value]
```

结果为 `[0,"receive",false,40,false,42]`。a 和 b 都已有数据，按候选顺序选择 a；b 的消息仍留在 b 中。

```chaos example=select-reuse
let c=channel(2);
let offer=send_case(c,{number:21});
let first=await channel_select([offer]);
await channel_select([offer]);
[first.op,first.value,(await channel_receive(c)).value.number,
 (await channel_receive(c)).value.number]
```

结果为 `["send",null,21,21]`。复用同一候选不意味着复用同一次提交；每个选择拥有自己的捕获与队列注册。

## 完成值与仲裁规则

`channel_select(cases:List,timeout:Number|Null=null) -> Task` 要求非空 ChannelCase 列表。它复制列表顺序，重复的候选身份保留各自位置，不去重。完成值固定为：

| 字段 | 含义 |
| --- | --- |
| index | 选中候选在原列表的零基序号；超时为 null |
| op | receive／send；超时为 null |
| done | 接收已关闭且排空的通道时为 true，其他成功结果为 false |
| value | 接收的消息；发送、EOF 或超时为 null |
| timedOut | 期限到达且没有提交收发时为 true |

选择首次注册时，已有多个就绪候选按输入顺序偏置选择；这不是随机仲裁，也不保证低优先级候选最终获选。挂起后的选择由实际通道状态变化及通道内可兼容队列顺序触发，不承诺跨通道的全局输入优先级；同一选择的发送与接收不会互相配对。两个不同选择可以原子配对，双方先确定各自唯一赢家并摘除所有败选注册，再发布完成通知。对端选择随这一次会合选中对应候选，不另行重选其他分支。一个选择不能因为候选指向多个通道而变成多个独立收发 Task。

```chaos example=select-rendezvous
let a=channel();
let b=channel();
let left=spawn(channel_select([send_case(a,21),receive_case(b)]));
let right=spawn(channel_select([receive_case(a),send_case(b,42)]));
map(fn(result){[result.index,result.op,result.value]},await gather([left,right]))
```

结果为 `[[0,"send",null],[0,"receive",21]]`。未选中的 b 分支不会遗留注册或消息。

## 期限、关闭与取消

`timeout` 是非负有限 Number，可为小数；Bool、负数、非数字、非有限数不合法。null 不设置此选择的期限。期限从选择实际启动时按单调时钟计算；timeout=0 先检查可提交候选，再返回超时。就绪条件以调度器已经可原子提交的状态为准，不承诺外部事件的日历时间顺序。空列表始终是 argument_error，不用空选择替代 sleep。

```chaos example=select-timeout
let c=channel();
let result=await channel_select([receive_case(c)],timeout=0);
[result.index,result.op,result.done,result.value,result.timedOut,
 channel_info(c).waitingReceivers]
```

结果为 `[null,null,false,null,true,0]`。超时撤销本选择全部尚未提交的候选，不关闭共享通道。显式 cancel、TaskGroup 失败、私有原生 Await 中断也只撤销本选择拥有的注册。提交完成后的 cancel 返回 false，不撤回已交付消息。

关闭通道上的接收仍可读完缓冲后得到 EOF；关闭通道上的发送是一个就绪的失败候选，选中时抛出 channel_closed。首次注册的多个就绪候选仍遵循输入顺序，不能自动跳过失败的发送分支。挂起后关闭也是实际通道事件，可能让该通道上的候选获选。

```chaos example=select-closed
let c=channel();
channel_close(c);
let result=await channel_select([receive_case(c)]);
[result.index,result.op,result.done,result.value,result.timedOut]
```

结果为 `[0,"receive",true,null,false]`。非法候选、跨解释器／执行周期／录制范围的候选在整批预检时失败，不能留下半批排队。启动和交付时继续复验活载荷。硬错误保持原有高优先级，不会包装为普通成功结果；无法推进且没有效果或期限可以唤醒的选择报告 task_deadlock。

## 作为智能体函数的控制结构

```chaos example=select-workers
let replies=channel();
let stop=channel();
async fn worker(n) {
  await sleep(0);
  await channel_send(replies,n*2)
}
let answer=async with task_group() as workers {
  workers.spawn(worker(21));
  let ready=await channel_select([receive_case(replies),receive_case(stop)],timeout=1);
  [ready.index,ready.value,ready.timedOut]
};
[answer,channel_info(stop).waitingReceivers]
```

结果为 `[[0,42,false],0]`。worker 可以调用有权限的 Agent、Team 角色或 Session，再发送结果。用户函数决定分支、恢复和停止策略；运行时不通过角色名称或提示词关键词猜测流程。TaskGroup 继续负责明确接纳的工作生命周期；选择结束不自动取消生产者或其他借用任务。

## 资源与持久化边界

候选与通道一样只属于当前执行周期及录制范围。每轮候选数量上限10000；所有尚未终止的选择（包括冷 Task）共同最多保留10000个候选注册位置。发送模板和每次选择的消息捕获共同使用现有 Channel 共享配额：10000 slots、100000 nodes、1000000 chars。失败、取消和终态释放选择捕获；可复用模板保留到 epoch 结束，因此循环创建新的 send_case 即使每次发送成功也可能耗尽模板配额。重复选择时应复用语义相同的候选。引用的活对象仍受其自己的生命周期约束。

暂停调试时语言停止推进，单调时间仍继续经过；恢复后处理期限，页面不自行模拟倒计时。运行时间／步数预算仍有效。跨轮收件箱和定时唤醒使用 [Process](PROCESSES.md)，不能通过 JSON 描述恢复活候选、队列或暂停栈。

## 源码、提示与运行态一致

ChannelCase 是内建类型名；已有同名 class/data 定义需要重命名。选择使用已有普通调用、List、await AST；宏、格式化、源码／IR、CLI、SDK 和 HTTP 共用实现。IDE 为构造函数、case_info、直接 await 结果提供真实签名与成员提示；可变或动态未知表达式仍保守处理。

TaskInfo 对选择增加 selection：caseCount、cases、casesTruncated、index。cases 最多100项，只列 index、op、channelId，不含消息。选择本身 kind 为 channel_select，挂起等待时 phase 为 channel_select；没有伪造的成员 Task，channelId 为 null。timing 沿用固定四字段，仅 timeoutSeconds 与活动期限的 remainingSeconds 适用，冷态／终态／无期限剩余为 null。非选择任务的 selection 为 null。ChannelInfo 中等待数量按候选注册计数，同一选择在同一通道有多个候选时可以出现重复 Task ID；展示与截断遵循实际队列。

[task_wait](WAITS.md) 观察任务完成分区，启动的每个收发 Task 都可能提交；channel_select 仲裁尚未提交的候选，只提交赢家。两者可组合，但语义不互相替代。
