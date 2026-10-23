# 词法取消屏蔽

`CancelShield` 是当前解释器中的一等资源值。`cancel_shield()` 创建它，已有 `with` 或 `async with` 语法进入它；进入与退出本身不会启动新的任务或调用模型。保护范围属于执行该语句的逻辑 Task，普通函数、智能体函数和语言工具处理函数共用同一 AST 调度器。

## 进入与查看

```chaos example=shield-basic
async fn protect(value:Int) {
  with cancel_shield() as region {
    await sleep(0);
    let status = cancel_shield_info(region);
    [value, status.state, status.depth]
  }
}
await protect(42)
```

结果为 `[42,"active",1]`。管理器只能进入一次，状态由 `new` 经过 `active` 变为 `closed`。必须在正在执行的 Task 中进入；模块顶层可先调用一个 `async fn` 再 `await`。普通同步函数如果由 Task 调用，可以直接使用 `with`。`async with` 接受相同的管理器，其取消语义相同。

| 原语 | 结果 |
|---|---|
| `cancel_shield()` | 新的 `CancelShield` |
| `cancel_shield_info(shield)` | 状态、所属任务、当前深度和取消状态的有界 Record |

`cancel_shield_info` 返回 `id`、`state`、`taskId`、`location`、`cancellationRequested`、`cancellationDeferred`、`depth`。进入前 `taskId` 为 null，活动时 `depth` 是该管理器在任务保护栈中的位置，最外层为 1；关闭后深度为 0。修改返回记录不会修改调度器。管理器没有可写开关或清除取消请求的方法。

## 取消请求与实际交付

```chaos example=shield-deferred
let entered = channel();
let release = channel();
var notes = [];
async fn commit() {
  with cancel_shield() as region {
    await channel_send(entered, null);
    await channel_receive(release);
    notes = append(notes, "finished");
  }
  notes = append(notes, "after");
}
let job = spawn(commit());
await channel_receive(entered);
cancel(job);
let during = task_info(job);
await channel_send(release, null);
await task_wait([job]);
[during.cancellationRequested, during.cancellationDeferred,
 during.shieldDepth, task_info(job).state, notes]
```

结果为 `[true,true,1,"cancelled",["finished"]]`。取消请求在保护范围内保持待交付，已有的宿主效果不会因为该局部请求立即收到取消事件。离开最外层保护范围时，原取消继续传播，`"after"` 不会被追加；保护范围不会把被取消的任务变成成功。上例用 `task_wait` 观察结束；直接 `await job` 仍会传播不可捕获的任务取消。

`task_info` 的 `cancellationRequested` 包括尚未交付的请求；`cancellationDeferred` 表示目前有被保护范围暂缓的局部取消或任务组中断；`shieldDepth` 是当前活动管理器数量。保护范围嵌套时，内层退出不会穿透仍活动的外层。

`cancel(task)`、gather／TaskGroup 为收束成员发出的局部取消，以及组体的 `GroupAbort` 中断可以暂缓。请求来源继续保留，组内收束取消不会掩盖最初的普通失败。已经交付的取消不能通过后来创建管理器撤销；保护范围内等待另一个已经取消的 Task 仍会收到该任务的失败，不能将它当作对自己的可延迟请求。

## 与资源、智能体和持久状态组合

把需要完成的资源作用域放在保护范围里面，正常资源退出和 finally 在局部取消交付前完成：

```chaos
async fn store_result(value) {
  with cancel_shield() as region {
    with exit_stack() as resources {
      resources.callback(fn() { tool("release_resource", null) });
      tool("write_result", value)
    }
  }
}
```

这里的两个工具必须由宿主注册并授权；展示和语言分析不会调用它们。Agent、Team、AgentTool、持久 AgentSession 的语言处理函数使用相同的能力、预算和执行证据。保护范围不会扩大工具权限，也不会自动授予外部写入权限。父任务与新建任务分别持有自己的保护栈；子任务、借用任务和团队成员不会隐式继承父任务的保护。

语言工具的私有子 Task 如果主动进入保护范围，取消调用者仍要等待该子任务实际结束；模型协议不会因为该范围而伪造成功回复。资源获取、工具副作用、Session 消息和 Process 源码／状态提交保持各自现有事务边界，取消屏蔽不是回滚、事务或提交保证。需要源码与 Process 状态共同提交时使用[原子演进](PROCESS_EVOLUTION.md)。

## 始终有效的边界

运行级取消、执行周期结束、步数／时间／数量预算耗尽、宿主硬错误始终可以终止保护范围。它们沿用现有硬控制规则，不为用户 finally 或资源退出追加预算。不合作的宿主回调仍可能延长排空时间；强制杀进程没有语言清理保证。

管理器受解释器、执行周期、录制范围和能力来源约束；不能用 JSON 还原，也不能跨周期复用。`ExitStack`／`AsyncExitStack` 不接管它，因为取消范围必须与真实词法栈一致。生成器不能把在自身内部进入的保护范围跨 `yield` 交给调用者；在外部保护范围内消费另一生成器是允许的。跨重启恢复活栈、一般性效果事务和完整 Python/Lisp 生态仍不由此获得。

## 编辑、调试和接口

源码、结构 IR、CLI、SDK 与 HTTP 项目运行复用相同的 `with` 节点。代码颜色、签名、类型与字段提示来自共享原生元数据，别名和名称遮蔽遵循真实绑定身份。暂停时调试器显示实际保护范围、Task 深度及待交付状态；安全快照不会执行用户 getter。格式化只调整源码排版，GUI 和无界面提交仍使用现有版本检查。

这个设计参考了 [Trio 的词法取消范围](https://trio.readthedocs.io/en/stable/reference-core.html#cancellation-and-timeouts)和 [asyncio 对受保护任务的区分](https://docs.python.org/3/library/asyncio-task.html#shielding-from-cancellation)，具体行为由本页的 Chaos 任务、硬控制与归属规则规定；它不是 Python API 兼容层。


## 原生协议操作的取消来源

语言资源、AgentTool、Session 与 Bot 的回调使用同一 Task 调度器。回调创建并等待的私有 Task 若已进入调用方的同一实际任务组，其取消清理沿该组身份记录；等待会真正排空操作，不将这次内部清理误记成新的外部取消。

原失败或指导过期仍决定展开结果。这个规则只处理已由协议创建、且调用方与目标 Task 确属同组／同调度器／同执行周期的操作；借用 Task、跨组操作和没有同组身份的操作继续保留原来的独立来源。显式取消、全局取消、其他组／Join 的取消及真实宿主中断不被吸收，也不通过错误文字或关键词判断。

Bot 的过期决策可以按原合同进入 needs_reconciliation，保存已完成／不确定动作前缀，不自动重放。真正的取消和硬资源限制仍沿原错误合同传播；内部来源修正不回滚已经发生的工具、程序采用或物理效果。
