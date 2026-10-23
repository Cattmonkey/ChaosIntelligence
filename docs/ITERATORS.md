# 用户迭代器与智能体数据流

Iterator 和 AsyncIterator 把用户定义的逐项计算接入已有 for、async for、next、collect 和 yield from。它们是当前运行内的语言句柄；协议方法可以调用 Agent 或 tool，仍使用当前 Task、项目权限、预算与取消机制。生成器仍有自己的 Generator／AsyncGenerator 类型和双向通信语义。

## 一个同步游标

可迭代对象通过 `__iter__` 返回游标；游标通过 `__next__` 返回**恰好含 done、value 两个键的 Record**。done 必须是 Bool。done=false 表示产出 value，done=true 表示完成，value 是完成值；null 本身可以是正常元素。

```chaos example=iterators-cursor
class Counter {
  var current: Int = 40;
  fn __iter__(self) { self }
  fn __next__(self) {
    if self.current < 42 {
      let value=self.current;
      self.current=self.current+1;
      {done: false, value: value}
    } else { {done: true, value: "finished"} }
  }
}
let cursor=iter(Counter());
[type_of(cursor),next(cursor),next(cursor),next(cursor),next(cursor)]
```

结果是 `["Iterator",{done:false,value:40},{done:false,value:41},{done:true,value:"finished"},{done:true,value:"finished"}]`。完成记录被缓存；重复 next 返回新的完成 Record，不再调用用户方法。缺键、多键、非 Bool 的 done、Task 等错误结果会报错，不会被当作正常结束。

`iter(value)` 接受 Iterator 或同步 Generator 时返回原句柄；List／Tuple／Set／String 得到新游标，字符串按 Unicode 码点迭代。Instance 必须提供类协议 `__iter__`，其结果必须是同步句柄或具有 `__next__` 的 Instance。不会再次调用返回实例的工厂。Record 不隐式迭代，也不自动转换同步／异步协议。

`next(rawInstance)` 可以直接使用该实例的 `__next__`，不调用 `__iter__`。协议通过类的 C3 方法顺序和描述符绑定查找，忽略实例动态新增的同名字段；现有但不可调用的协议会报错，不进行猜测式回退。

## 可重复遍历的容器

`__iter__` 每次获取游标都会执行。返回 self 适合一次性游标，返回新的迭代器或生成器适合可重复遍历的容器。生成器工厂是自然支持的结果类型：

```chaos example=iterators-factory
class Numbers {
  fn* __iter__(self) { yield 40; yield 42; return 84 }
}
let numbers=Numbers();
[collect(numbers),collect(numbers),collect([1,2]),collect("好!")]
```

结果为 `[[40,42],[40,42],[1,2],["好","!"]]`。collect 先获得游标，只收集产出值，丢弃完成值；最多收集 10,000 项，还受整个运行的步数与时间预算限制。从0.34起，map/filter/fold/any/all、函数星号展开和 apply 也消费此同步协议；短路、借用与输入预算见[函数组合](FUNCTION_COMPOSITION.md)。其他列表专用函数保持原契约。

工厂是否合法取决于返回值，不是声明风格。`fn* __iter__` 返回 Generator 合法，`async fn* __aiter__` 返回 AsyncGenerator 合法；工厂返回 Task 则拒绝，不为获取游标隐式 await。普通工厂内部调用 Agent 仍可以通过当前 Task 挂起等待效果。

## 异步逐项请求

`aiter(value)` 接受 AsyncIterator／AsyncGenerator，或调用实例的 `__aiter__` 获取异步句柄／具有 `__anext__` 的实例。`__anext__` 必须返回当前运行拥有的 Task，该 Task 完成后得到同样严格的 `{done,value}` Record。普通 fn 返回 Task 也可用；直接返回 Record 不满足异步步骤契约。

```chaos example=iterators-async
class AsyncCounter {
  var current: Int = 40;
  fn __aiter__(self) { self }
  async fn __anext__(self) {
    if self.current < 42 {
      let value=self.current;
      self.current=self.current+1;
      {done:false,value:value}
    } else { {done:true,value:84} }
  }
}
let cursor=aiter(AsyncCounter());
let first=anext(cursor);
[type_of(cursor),type_of(first),await first,await anext(cursor),await anext(cursor)]
```

结果为 `["AsyncIterator","Task",{done:false,value:40},{done:false,value:41},{done:true,value:84}]`。anext、asend、athrow、aclose、acollect 创建冷 Task；调度或 await 之前不解析步骤描述符、不调用步骤方法。每次步骤只等待协议返回的 Task 一次，嵌套 Task 不会自动展开为结果。

async for 支持异步可迭代对象；它在模块顶层、async fn 或 async fn* 中执行，逐项请求而不预取全部数据。普通 for 接受同步可迭代对象。两种循环都只获取一次游标，并为每一项建立独立循环变量绑定。break、return 或循环体错误离开循环时保留游标，不隐式关闭，因而可以继续消费。

## 共享别名和提前退出

同一执行周期、同一同步／异步模式、同一原始实例的所有适配操作共用一个句柄和游标状态。重复工厂调用仍会发生；若工厂再次返回同一实例，适配后的终态和锁不会重置。适配缓存最多保留 10,000 项，并在新的执行周期重建。

```chaos example=iterators-resume
class Cursor {
  var position=0;
  fn __iter__(self) { self }
  fn __next__(self) {
    if self.position<3 {
      let value=self.position;
      self.position=self.position+1;
      {done:false,value:value}
    } else { {done:true,value:99} }
  }
}
let raw=Cursor();
let first=iter(raw);
var seen=[];
for value in first { seen=append(seen,value); break }
let alias=iter(raw);
[seen,next(alias),collect(first),next(raw)]
```

结果为 `[[0],{done:false,value:1},[2],{done:true,value:99}]`。两个不同原始实例可以并发消费；同一实例的 raw 值与句柄别名不能并发推进或关闭。busy 从描述符绑定开始覆盖方法执行、Agent 效果和 await，最终在 finally 中释放。collect／acollect 对整个收集过程独占句柄，避免另一个消费者插入取走元素。

别名保护只作用于同一游标身份，不会替你隔离不同实例所共享的其它可变状态。实例与已经完成的外部效果不回滚。句柄不能跨 Framework 运行、REPL 提交周期或宿主进程恢复；仍存活的普通实例可以在新周期重新适配，但旧句柄会被拒绝。

## 可选的双向通信和关闭

| 同步协议 | 异步协议 | 调用与结果 |
| --- | --- | --- |
| `__iter__(self)` | `__aiter__(self)` | 获取游标；直接返回相应句柄或步骤实例 |
| `__next__(self)` | `__anext__(self)` | next／anext；同步严格步骤 Record，异步 Task 完成为步骤 Record |
| `__send__(self, value)` | `__asend__(self, value)` | send／asend 发送非 null 数据；结果要求与步骤相同 |
| `__throw__(self, value)` | `__athrow__(self, value)` | throw_into／athrow 注入语言 throw 的原始值；结果要求与步骤相同 |
| `__close__(self)` | `__aclose__(self)` | close／aclose；同步返回 null，异步 Task 完成为 null |

公开内建函数的参数名是 `iter(value)`、`aiter(value)`、`next(handle)`、`send(handle,value)`、`throw_into(handle,value)`、`close(handle)`、`collect(handle)`；异步版本为 anext、asend、athrow、aclose、acollect，使用相同的 handle／value 标签。

send(null) 使用 next 协议。缺失非 null send hook 会拒绝该操作，不推进或终结游标。缺失 throw hook 或已终态时，注入的语言异常直接传播，不推进状态。协议抛出的普通错误和取消使当前适配句柄终结；该失败本身传播给调用者，后续 next 返回 `{done:true,value:null}`。用户不能抛出宿主 Python StopIteration 来伪造正常结束。

```chaos example=iterators-bidirectional
class Mailbox {
  var closed=false;
  fn __iter__(self) { self }
  fn __next__(self) { {done:false,value:"ready"} }
  fn __send__(self, value) { {done:false,value:value+1} }
  fn __throw__(self, value) { {done:true,value:value} }
  fn __close__(self) { self.closed=true; null }
}
let raw=Mailbox();
let cursor=iter(raw);
let received=[next(cursor),send(cursor,41),throw_into(cursor,84)];
close(cursor);
[received,next(cursor),raw.closed]
```

结果为 `[[{done:false,value:"ready"},{done:false,value:42},{done:true,value:84}],{done:true,value:84},true]`。close 不清空已经缓存的正常完成值；每个适配句柄最多尝试一次关闭 hook。普通名为 close 的实例成员不属于特殊协议，不会被运行时自动调用。

## yield from 委托

```chaos example=iterators-delegation
class Source {
  var sent=false;
  fn __iter__(self) { self }
  fn __next__(self) {
    if self.sent { {done:true,value:84} }
    else { self.sent=true; {done:false,value:42} }
  }
}
fn* forward(source) { let result=yield from source; return result }
let stream=forward(Source());
[next(stream),next(stream)]
```

结果为 `[{done:false,value:42},{done:true,value:84}]`。yield from 返回被委托对象的完成值，并转发其支持的发送、语言 throw 与显式关闭。注入的普通 LanguageError 保持原错误，不伪装为用户 throw 的数据。同步委托拒绝异步对象；异步生成器可委托同步或异步源，一个实例同时暴露两种工厂时优先异步工厂。现有 Generator／AsyncGenerator 的双向行为保持不变，见[效果与流](EFFECTS.md)。

## 生命周期与失败

正常执行周期结束，包括普通 LanguageError 退出时，运行时先排空活动任务，再尝试关闭所有已登记但尚未尝试关闭的用户适配句柄；即使未消费、已经完成或步骤失败，也会尝试相应关闭 hook，之后排空清理新建的任务。设置关闭标记发生在调用 hook 之前；关闭成功或失败都使句柄 closed 并释放锁，不自动重试。没有 hook 时只关闭句柄。已有主错误继续传播，清理错误不会替换它；没有主错误时清理失败会让运行失败。

硬取消、已交付的单任务取消或资源上限是执行控制信号，不执行语言清理 hook；运行时标记内部句柄终态、释放锁并等待合作式效果线程退出。非合作宿主回调可能延迟退出，线程不能被安全强杀。取消不撤销已执行的 Agent、工具或实例状态修改。

Iterator／AsyncIterator 的类型、序列化与调试摘要只描述已知状态，不暴露内部目标、闭包或 owner，也不为了预览调用用户协议。JSON 中的描述只是普通数据，不可恢复活动游标。低层受信任同步 Evaluator 回调仍保留旧 live-callable ABI；这不是宿主 Python 沙箱，见[SDK](SDK.md)。

## 宏生成协议

```chaos example=iterators-macro
macro source(value) {
  quote {
    class Generated {
      fn* __iter__(self) { yield unquote(value); yield 2 }
    }
    Generated
  }
}
let self=40;
let Generated=source!(self);
[collect(Generated()),self]
```

结果为 `[[40,2],40]`。协议标签不重命名；类名、self 和其它词法绑定保持卫生，unquote 保留调用者身份。跨模块导出宏遵守原有规则。宏只生成 class／方法／循环／yield from 的 AST；pure 阶段不能运行 iter、aiter、next、Agent 或关闭 hook，syntax_literal 也不能嵌入活动句柄。更多规则见[宏规范](MACROS.md)。

## 实际 Registry 提供方

下面通过 Framework 的实际 Agent 调度链并发消费两个独立异步游标。提供方是确定性的本地回调，无需外部模型或密钥；步骤仍产生真实的 agent.completed 事件。

```python
from chaos import Final, Framework, Registry
from chaos.settings import Settings

registry = Registry()

@registry.provider("app.iterator")
def score(request, context):
    context.checkpoint()
    return Final(request.input + 1)

source = '''
agent worker {provider:"app.iterator",tools:[],maxSteps:1}
class Scores {
  var current=40;
  fn __aiter__(self) { self }
  async fn __anext__(self) {
    if self.current>42 { {done:true,value:84} }
    else {
      let value=self.current;
      self.current=self.current+2;
      {done:false,value:worker(value)}
    }
  }
}
await gather([acollect(Scores()),acollect(Scores())])
'''
with Framework(registry=registry, settings=Settings(":memory:")) as app:
    project = app.create_project("iterator-agent", source=source, capabilities=[])
    run = app.start(project["id"], project["version"], None, mode="source")
    assert run.result(timeout=5) == [[41, 43], [41, 43]]
    events = app.events(project["id"], run_id=run.id, limit=300)["items"]
    assert sum(event["type"] == "agent.completed" for event in events) == 4
```

这里并发的是两个不同的游标；同一游标重叠 anext／acollect 会被拒绝。Agent 接收显式输入数据，不接收活动游标或协议闭包；Framework／Registry、API 和异步 worker 继续使用 JSON 边界。

## 统一执行帧

获取、描述符调用、一次 Task 等待、步进、关闭、消费者和委托，以及 Generator／AsyncGenerator 的恢复与关闭，都由当前逻辑执行者的真实类型化帧驱动。帧保存正在使用的游标、待决参数、结果、错误和原权限；原协议签名、终态缓存、busy 范围与取消规则保持不变。普通函数、AgentTool、Team、Bot、Program 与源码／IR 入口共用这些状态。

整个执行周期的任务协调和注册表清理仍由原调度器负责，清理中的每次恢复／关闭进入同一帧实现；没有因此提供活动游标序列化、跨重启恢复或可复用 continuation。详见[执行帧](EXECUTION_FRAMES.md#迭代消费委托与推导的执行状态)。

## 与 Python 的区别

工厂、逐项请求和稳定终态参考 [Python 同步迭代协议](https://docs.python.org/3/library/stdtypes.html#iterator-types)及[异步迭代协议](https://docs.python.org/3/reference/datamodel.html#asynchronous-iterators)。Python 的 next 返回元素并通过 StopIteration 表示完成，anext 通过 StopAsyncIteration 完成；Chaos 统一使用 `{done,value}`，异步步骤必须返回本运行拥有的 Task，不接受任意宿主 awaitable。

可选 __send__／__throw__／__close__ 及异步对应协议是 Chaos 的明确约定，并非 Python 通用迭代器都具有的协议。已有异步生成器允许带完成值的 return 和 async yield from，也属于本系统扩展。提前停止时需要理解显式关闭与运行周期回收，而不是依赖宿主垃圾回收时机；Python 的相关清理规则见[官方生成器说明](https://docs.python.org/3/reference/expressions.html#asynchronous-generator-functions)。

本轮不加入 Python 的序列索引回退、iter(callable,sentinel)、next(default)、任意 asyncio 集成、跨进程恢复或完整 Python／Lisp 生态兼容。contains 不因为新增迭代器而自动回退到逐项遍历，纯模式匹配也不会运行迭代协议。

## 原生 Channel 消费

Channel 使用语言原生异步适配：aiter(channel) 每次创建独立消费游标，async for 与 acollect 通过自己的接收操作等待。并发游标竞争而非广播消息；break／aclose 游标不关闭共享 Channel。仅 channel_close 显式关闭共享消息流，随后排空缓冲再结束。见[消息通信](CHANNELS.md)。

## 推导式消费

原生列表／记录／集合推导与生成器表达式复用本页协议；`async for` 子句消费异步来源。每步释放 busy 状态，停止或关闭包装器不隐式关闭借用游标。内部 List／Tuple／Set／String 来源使用临时游标。时序、配额与例子见[推导式](COMPREHENSIONS.md)。

局部取消可由显式[CancelShield](CANCELLATION.md)延迟到最外层词法范围退出后交付；这不延迟全局取消或预算耗尽。

## 耗尽分支

普通／模式 for 和 async for 可接 else。迭代器报告 done 时（包括空输入）进入该分支；break 与其他异常退出不进入。借用游标的推进与关闭合同不变，不因增加完成块而自动关闭输入或吞掉来源／推进中的外层控制信号。说明及例子见[正常完成分支](CONTROL_COMPLETION.md)。
