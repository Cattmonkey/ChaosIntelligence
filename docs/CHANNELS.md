# Channel：语言原生消息通信

Channel 将函数、智能体和工具任务连接为显式消息流。它是当前解释器／执行周期的一等值，调度、等待、背压与取消都在语言调度器中完成，不依赖提示词识别、宿主线程队列或网页轮询。

## 从生产者到消费者

```chaos example=channels-pipeline
async fn produce(out:Channel, values:List) {
    for value in values { await channel_send(out,value*2) }
    channel_close(out);
    null
}
let messages:Channel=channel(1);
let result=async with task_group() as workers {
    workers.spawn(produce(messages,[20,21]));
    await acollect(messages)
};
[result,type_of(messages),channel_info(messages).state]
```

结果为 `[[40,42],"Channel","closed"]`。生产者负责显式关闭通道；TaskGroup 管理任务生命周期，不会隐式关闭通道。缓冲区满时，发送者等待消费者取值，这就是背压；容量为 0 时不保存缓冲值，发送与接收必须配对才能完成。

`async for value in messages { ... }` 同样逐项接收。每次 aiter／异步循环／acollect 建立独立消费游标；多个消费者竞争取得消息，一条消息只交给一个消费者，不是广播。循环提前 break 或 aclose 一个消费游标，不关闭共享 Channel。同步 for／iter 不能消费 Channel。等待一个保持开放且没有后续生产者的通道不会凭空得到结束信号；无可运行任务与可完成效果时，运行时报告 task_deadlock。

## 函数签名

| 函数 | 结果与时机 |
| --- | --- |
| `channel(capacity:Int=0) -> Channel` | 建立当前执行周期的通道，容量必须是非负 Int |
| `channel_send(channel:Channel,value:Any) -> Task` | 创建冷 Task；启动时发送，成功完成值为 null |
| `channel_receive(channel:Channel) -> Task` | 创建冷 Task；完成值为严格的 `{done:Bool,value:Any}` |
| `channel_close(channel:Channel) -> Bool` | 立即关闭；第一次返回 true，再次返回 false |
| `channel_info(channel:Channel) -> Record` | 读取有界快照，不启动、等待或观察任务结果 |

当前容量为 0..10000，单执行周期最多10000个 Channel。未交付消息（包括冷 send 捕获、等待发送及缓冲）在整个周期共享10000槽、100000个语言数据节点和1000000个字符串字符上限；超过上限抛出不可捕获的 ResourceLimit。函数／实例按活值身份计节点，不遍历闭包或 getter；已交付结果仍受既有 Task 和值限制。资源上限不是无限内存保证。

创建发送或接收 Task 不入队、不占缓冲槽。使用 await、spawn、gather 或 TaskGroup.spawn 才启动。FIFO 按操作实际注册到等待队列的顺序，不按冷 Task 的创建顺序，也不承诺多个 Agent 的模型响应按启动顺序完成。

```chaos example=channels-cold-and-close
let messages=channel(2);
let pending=channel_send(messages,null);
let before=channel_info(messages).buffered;
await pending;
await channel_send(messages,42);
let first=channel_close(messages);
let second=channel_close(messages);
[before,first,second,await channel_receive(messages),
 await channel_receive(messages),await channel_receive(messages)]
```

结果为 `[0,true,false,{done:false,value:null},{done:false,value:42},{done:true,value:null}]`。null 是合法消息；只有 done=true 才表示流结束。关闭拒绝尚未提交和后续启动的发送，以 channel_closed 报错；已经缓冲的值仍按顺序可读，读完后所有接收都得到 done=true。关闭操作不取消已经成功的发送。

## 取消与原子提交

配对或写入缓冲是消息的提交点。原生 send／receive 操作在这一点原子完成自己的 Task；随后 cancel 返回 false，不会把已提交消息描述为撤回。尚未配对／写入的发送和尚未取得消息的接收被取消时，会从等待队列移除，不能继续消耗容量或窃取后续消息。

普通 Task 的借用等待规则保持不变。原生通道操作有明确的私有等待规则：await 一个由当前逻辑任务创建且仍未启动的 channel_send／channel_receive Task 时，本次等待拥有该操作；这也包括当前任务预先创建的冷操作。等待者中断时撤销尚未提交的私有操作。其他任务创建或已经启动的通道操作仍是借用，取消等待者不会取消它。需要将操作显式纳入组生命周期时可使用 TaskGroup.spawn；异步迭代自行创建的接收操作同样由消费过程拥有。组失败和运行退出清理等待项，硬错误与总预算继续使用原来的优先级；已发生的消息提交和外部工具效果不自动回滚。

## 函数与智能体也是消息

```chaos example=channels-callable
let messages=channel(1);
let worker:Function=fn(x:Int)->Int { x+1 };
await channel_send(messages,{action:worker,input:41});
channel_close(messages);
let message=await channel_receive(messages);
message.value.action(message.value.input)
```

结果为 `42`。List／Tuple／Record 在创建 send Task 时递归复制；持久 Set 保持原生类型且成员递归纳入消息检查与遍历预算，Instance 成员在此验证自身所有权，字段访问、调用与捕获仍沿既有 owner／epoch／能力检查；同一解释器内的函数、Agent 和其他合规活值保留身份，并不序列化成源码或重新推断提示词。对闭包和实例来说，这也意味着原来的共享状态仍然共享，发送消息不会自动克隆它们。

可把上例的普通函数替换成项目中已授权的 Agent、Team 角色函数或调用它们的异步函数；接收者调用该值时仍经过原有 Context、能力交集、预算与效果记录。传递可调用值不会增加能力，也不会创建新的模型后端。通道操作的创建、实际启动和消息交付都会检查适用的解释器、执行周期及录制范围；不能通过嵌套消息把外部活句柄带入另一个执行范围。活任务在排队期间可能进入技能录制状态，因此交付前也必须复验：失效消息被释放，接收者报告相应归属错误；仍在等待配对的发送者也失败，已经写入缓冲并完成的发送者保持成功，不倒改已提交结果。

## 生命周期与持久系统

Channel 只属于一次活动执行周期。跨 Evaluator、跨提交周期、跨录制 scope 的使用会在效果发生前拒绝。Channel 没有可写成员或 JSON 恢复构造器；输出／工具边界只得到不可还原的描述，不包含消息内容、队列或执行能力。

[Process 邮箱](PROCESSES.md)负责跨轮次和重启的持久 JSON 消息、事务领取和确认；Channel 负责一轮内部的函数通信。持续 dot 可以在每个 Process 轮次内建立 Channel 管道，将最终普通数据通过原有状态／邮箱协议提交。嵌套 Program 也使用自己的解释器，不能共享父解释器的活 Channel。

0.33 的 [channel_select](SELECT.md) 提供原子多分支收发和等待期限。本版本仍不提供跨进程通道、广播、消息重试保证或暂停栈恢复。0.49 的[词法取消屏蔽](CANCELLATION.md)可显式保护当前 Task；通道操作和新建收发 Task 不会自动获得保护。

## 状态、代码提示和可视化

channel_info 返回新 Record：id、state、capacity、buffered、waitingSenders、waitingReceivers、sendTaskIds、receiveTaskIds、sendersTruncated、receiversTruncated、location。ID 列表最多100项；快照只给数量和身份，不泄漏消息内容。state 为 open 或 closed，关闭后仍可能 buffered>0。任务元数据中的 channelId 指向实际通道；kind 为 channel_send／channel_receive，等待阶段同名。

IDE 根据真实绑定提供五个函数的签名、Channel 注解、接收完成值的 done/value 字段、状态字段与悬停提示。用户同名变量覆盖内建函数时不凭名称猜测其含义；消息 value 保守推断为 Any。语法着色、自动换行格式化、结构编辑、无界面 SDK／CLI 和 HTTP 使用同一个语言核心。在线与离线文档中的代码保留原文复制、语义颜色、提示和长行换行。

调试暂停时显示同一调度器冻结的有界 Channel 状态及发送／接收 Task 身份，不执行用户 getter、不展示消息正文。恢复运行后清除旧暂停卡片，下一次暂停再刷新；这不是另一套并行控制通道。

## 与完成等待组合

[task_wait](WAITS.md) 可以等待已创建的收发 Task 或其期限，但不能充当原子 select：被启动的多个操作都可能已经提交。超时和取消等待本身不会撤回消息，也不会取消借用操作；如需结束未提交接收，应显式 cancel 相应 Task。

原子只选一个尚未提交的收发请使用 [ChannelCase 与 channel_select](SELECT.md)。选择候选与普通收发共享通道队列，等待数量按候选注册计数；同一选择重复指向通道时，等待 Task ID 可以重复。旧单操作 Task 的行为保持不变。

## 元组消息

Tuple 与 List／Record 一样参与递归复制和嵌套活值检查，类型在当前执行周期内保留。可以发送 `(action,input)` 后解构调用，权限、生命周期与录制范围检查不变。输出描述不会恢复活函数或队列，详见[元组消息示例](TUPLES.md#函数及智能体作为元素)。
