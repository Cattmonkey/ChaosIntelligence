# 动态资源栈与智能体组合

当资源数量由输入、计划或智能体的结果决定时，可以用资源栈登记已经获取的资源。栈退出时按照登记的相反顺序清理；中途获取失败，外层栈仍负责释放先前成功进入的资源。这是语言运行时能力，源码、结构 IR、SDK、HTTP、CLI 和 IDE 共用实现。

## 创建和使用

`import "std/context" as Context` 提供 `Context.ExitStack(max_entries=1024)` 和 `Context.AsyncExitStack(max_entries=1024)` 工厂。底层内置 `exit_stack(asynchronous=false,max_entries=1024)` 返回对应的原生句柄。它们不是 Python 宿主类，不能通过 JSON 重建活动资源。

```chaos example=stack-dynamic-resources
import "std/context" as Context;
var events=[];
@Context.contextmanager
fn* resource(value:Int) {
  events=append(events,value);
  try { yield value }
  finally { events=append(events,-value) }
}
let answer=with Context.ExitStack() as stack {
  let values=[stack.enter_context(resource(n)) for n in [20,22]];
  sum(values)
};
[answer,events]
```

结果为 `[42,[20,22,-22,-20]]`。`enter_context(manager)` 返回该管理器的进入值，不是管理器本身；只有成功进入后才登记退出操作。进入前保存退出绑定，所以后续修改类方法不会改变已经登记的清理。

栈也可以在 with 之外使用，再显式调用 close/aclose。每个栈只有一个生命周期：关闭后不能再次进入或添加资源；重复关闭不重复执行回调。资源作用域本身返回 body 的结果。原生栈不依赖垃圾回收触发清理，执行周期结束时由解释器收束仍开放的栈；不要把句柄保存到下一次执行中使用。

## 同步接口

| 方法 | 行为 |
| --- | --- |
| `enter_context(manager)` | 进入同步管理器，保存退出操作，返回进入值 |
| `callback(function,*args,**kwargs)` | 登记普通清理函数及参数，返回 function |
| `push(exit_function)` | 登记退出函数或用户类实例的 __exit__，返回传入对象 |
| `pop_all()` | 将全部登记项原子转移到同种类的新栈，原栈清空但继续开放 |
| `close()` | 以无 body 错误的状态立即关闭同步栈 |

普通 callback 不接收 body 错误，其返回值不能抑制错误；它抛出的错误仍参与退出传播。为避免把未运行的计算误认为已经清理，普通 callback 不接受 Task、Generator 或 AsyncGenerator 作为完成结果。使用需要等待的清理函数时，应登记为异步回调。

callback 与 push_async_callback 的第一个函数参数必须按位置传入；其余具名参数全部转发给清理函数，包括名为 function 的参数。push 与 push_async_exit 接受用户类实例时，只捕获相应退出钩子，不调用进入钩子。原生 Computer、生成器管理器、资源栈和 TaskGroup 不接受这种退出登记，应使用支持的 enter_context 或 enter_async_context。

push 的错误参数与 catch 一样是隔离的 `{code,message,value,location}` 记录。返回值必须严格为 Bool 或 null；只有 true 可以抑制普通可捕获错误。错误记录不赋予宿主异常身份，`throw error` 仍表示抛出该记录值的新用户错误。

```chaos example=stack-error-transformation
import "std/context" as Context;
var seen=[];
fn recover(error) { seen=append(seen,error.value); true }
fn replace_error(error) { throw "cleanup" }
let result=with Context.ExitStack() as stack {
  stack.push(recover);
  stack.push(replace_error);
  throw "body"
};
[result,seen]
```

结果 `[null,["cleanup"]]`。后登记的退出函数先把 body 错误替换成 cleanup 错误，较早登记的退出函数看到更新后的错误并将其抑制。没有抑制时传播最后仍待处理的错误；生成器管理器收到真实的内部错误，保留原始类型和位置。退出栈不是事务，不能撤销已经发生的网络、文件或电脑操作。

## 异步和混合资源

AsyncExitStack 支持上述同步登记方法，并增加：

| 方法 | 行为 |
| --- | --- |
| `enter_async_context(manager)` | 返回冷 Task；等待时进入异步管理器，完成值是进入值 |
| `push_async_callback(function,*args,**kwargs)` | 登记清理函数，退出时等待它返回的一层 Task |
| `push_async_exit(exit_function)` | 登记异步错误处理函数，等待后必须得到 Bool 或 null |
| `aclose()` | 返回冷 Task；等待后关闭全部同步和异步登记项 |

AsyncExitStack 使用 async with 或 await aclose，没有隐式同步 close。异步回调等待一层 Task 后仍不能以另一冷 Task／流作为清理完成结果；不会自动递归等待。仅构造进入或关闭的 Task 不执行资源操作。

```chaos example=stack-mixed-async
import "std/context" as Context;
var events=[];
async fn note(value) { events=append(events,value); null }
@Context.contextmanager
fn* plain(value) {
  try { yield value }
  finally { events=append(events,"sync") }
}
@Context.asynccontextmanager
async fn* pending(value) {
  try { yield value }
  finally { await note("async") }
}
let result=async with Context.AsyncExitStack() as stack {
  let first=stack.enter_context(plain(20));
  let second=await stack.enter_async_context(pending(22));
  stack.push_async_callback(note,"callback");
  first+second
};
[result,events]
```

结果 `[42,["callback","async","sync"]]`，混合登记仍遵守同一逆序，不把异步清理移到另一个解释器或后台应用逻辑中。

## 转移释放责任

```chaos example=stack-transfer
import "std/context" as Context;
var events=[];
fn mark(value) { events=append(events,value); null }
let held=with Context.ExitStack() as stack {
  stack.callback(mark,"released");
  stack.pop_all()
};
let before=events;
held.close();
[before,events]
```

结果 `[[],["released"]]`。pop_all 不执行清理，而是转移全部登记项的释放责任；仍在同一解释器、执行周期和录制范围内。它不复制资源，也不生成可跨进程恢复的租约。转移后应明确安排新栈的关闭位置。

## 智能体、电脑与录制

资源工厂的获取和清理可以调用 Agent；登记的回调也可以是具有对应调用契约的智能体或工具包装函数。所有效果共享原有能力交集、预算、取消和执行证据，不会因为加入栈获得更多权限。持久 AgentSession 不会自动被关闭，需要显式登记自己的 session_close 策略。

Computer 可通过 `await stack.enter_async_context(await computer_open(url))` 进入异步栈。临时 Computer 在释放时关闭；具名 computer_connect 的资源范围按原协议归还租约。对多个 URL 循环登记即可实现由输入驱动的多浏览器智能体；打开失败时先前已登记的浏览器仍由栈管理。详细电脑协议见[Computer](COMPUTERS.md)。

录制技能中创建的栈属于该录制范围，清理必须在录制封闭前完成；不能借栈把工具调用带到录制外继续执行。栈不会把录制中的句柄转换为普通可持久 JSON。详见[目标、计划与技能](AGENT_MODES.md)。

## 控制边界

普通 return、break、continue 和生成器关闭保留原控制流，并执行已登记的清理。普通退出错误可被更早登记的退出函数抑制，但不能把原本的非异常控制流吞掉。TaskGroup 的普通失败可以触发清理，其结构化失败不能被栈伪装为成功。

硬控制、资源限制或宿主中断沿用 finally 的边界，跳过后续任意用户清理，仅执行运行时与宿主的必要收束。取消不是任意清理代码执行成功的保证。

max_entries 为 0 到 10000 的 Int，默认 1024；每次执行最多创建 10000 个栈，同时最多登记 10000 项。原生所有权嵌套还受深度限制，不能形成循环。超过容量或深度属于 ResourceLimit 硬控制，遵守前述跳过用户清理的规则。

登记数量受 max_entries 及执行预算限制；栈在获取资源前检查容量和忙状态，不先产生无法登记的资源。等待进入／退出期间不能重入同一个栈。外来、过期或跨录制范围的句柄会被拒绝。

原生 TaskGroup 需要 AST 组体持有其结构化生命期，没有可以拆开的 enter/exit，因此不能通过 enter_context/enter_async_context 动态登记。CancelShield 同样只能通过词法 with／async with 进入，不能动态登记到资源栈。需要暂缓局部取消时，将资源栈放入[取消屏蔽范围](CANCELLATION.md)。资源栈本身不提供取消屏蔽、跨重启活栈恢复或通用资源事务。

IDE 与 LSP 根据真实类型提供方法、参数、进入值和 awaited 值提示；未知类型保留 Any。分析与格式化不执行获取、清理、智能体或工具。参考设计来自 [contextlib 的资源栈](https://docs.python.org/3/library/contextlib.html#contextlib.ExitStack)，实际行为以本页及[资源作用域](CONTEXTS.md)的 Chaos 契约为准。


## 栈操作的执行状态

登记、进入／退出、pop_all、当前清理项、逆序展开、待决错误／抑制状态，以及录制范围终结由同一逻辑执行者上的[真实执行帧](EXECUTION_FRAMES.md#资源协议与动态上下文的执行状态)保存。预留、忙锁和父子资源拥有关系仍按原规则建立与释放；等待期间不会重新获取已进入资源，也不会重放已经执行的清理。同步租约记账、原生设备操作和整个执行周期的调度协调保留原边界。该状态用于实际执行及预算检查，不是可以通过 JSON 恢复的活动资源快照。


### 在指定上下文中释放动态资源

```chaos
import "std/context" as C;
var released=[];
@C.contextmanager
fn* resource(value){try{yield value}finally{released=append(released,value)}}
let trace=context_var("trace","root");let selected=context();
let answer=selected.run(fn(){
 trace.set("worker");
 with C.ExitStack() as stack{
  let first=stack.enter_context(resource(20));let second=stack.enter_context(resource(22));first+second
 }
});
[answer,released,trace.get(),selected[trace]]
```

结果为 `[42,[22,20],"root","worker"]`。资源按逆序释放，调用者的 Context 恢复；所选 Context 保留其自己的绑定。
