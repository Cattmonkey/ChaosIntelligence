# 惰性流与函数管道

`std/iter` 是随运行包安装的 Chaos 源码模块，27个导出由普通函数、Generator 和 AsyncGenerator 实现。每次推进才拉取需要的输入；Agent、Team 角色与普通回调走相同调用和权限路径。原有全局 map／filter／fold 及 std/list 仍保持急切消费语义。

## 先组合，再消费

```chaos example=streams-pipeline
import "std/iter" as I;
let evens = I.filter(fn(value) { value % 2 == 0 }, I.count(40));
let results = I.map(fn(value) { value + 2 }, evens);
collect(I.take(results, 2))
```

结果为 `[42,44]`。这些工厂是普通函数：参数绑定、Function／Int 契约和有限选项检查在调用时发生，合法调用返回冷生成器；直到第一次 next／anext 才取得输入游标或执行回调。调用前对实参表达式自身的求值仍遵循普通语言顺序。`take(source,0)` 连输入游标也不取得；达到数量后不会额外读取一项以探测结束。

每个返回的流都只有一个推进位置，不会缓存所有结果以支持重放。多个别名共享该位置；要重新执行，应重新调用工厂并提供可重新迭代的数据源或新生成器。这里的惰性是按需消费，不改变语言整体的急切求值或默认参数时机。

## 同步与异步接口

导入 `import "std/iter" as I;` 后使用下表。同步变换返回 Generator，a前缀对应变换返回 AsyncGenerator，参数名、顺序和默认值相同。

| 同步接口 | 对应异步接口 | 消费与结果 |
|---|---|---|
| `map(mapper:Function,source)` | `amap(mapper,source)` | 逐项映射 |
| `filter(predicate:Function,source)` | `afilter(predicate,source)` | 严格 Bool 为 true 时保留原项 |
| `flat_map(mapper:Function,source)` | `aflat_map(mapper,source)` | 每项映射为来源，再逐项展开一层 |
| `take(source,count:Int)` | `atake(source,count)` | 最多前 count 项；count 非负，不预读 |
| `drop(source,count:Int)` | `adrop(source,count)` | 丢弃最多 count 项后继续输出 |
| `take_while(predicate:Function,source)` | `atake_while(predicate,source)` | 首个 false 项已消费但不输出，此后停止 |
| `drop_while(predicate:Function,source)` | `adrop_while(predicate,source)` | 首个 false 项开始输出，此后不再调用谓词 |
| `enumerate(source,start:Int=0)` | `aenumerate(source,start=0)` | 输出 `(index,value)` Tuple，索引逐次加1 |
| `zip(*sources)` | `azip(*sources)` | 从左至右推进，到最短来源结束，输出 Tuple；零来源为空 |
| `chain(*sources)` | `achain(*sources)` | 按来源顺序连接；零来源为空 |
| `batches(source,size:Int)` | `abatches(source,size)` | 输出 Tuple 批次，末批保留不足 size 的项目 |
| `scan(reducer:Function,initial,source)` | `ascan(reducer,initial,source)` | 先输出 initial，再依次输出 reducer(accumulator,item) |

另有 `from_sync(source) -> AsyncGenerator`，显式把同步来源逐项放入异步流；`count(start:Int=0,step:Int=1) -> Generator` 持续产生整数，step 可为0；`repeat(value,count=null) -> Generator` 重复同一个值，count 为 null 时不设次数，其余须为非负 Int。repeat 不克隆值。batches 的 size 为1至10000；所有 Int 参数严格拒绝 Bool 和浮点数。

同步来源沿 iter／next 协议，支持原生序列、集合、映射键、String、用户协议和生成器。Record 仍需显式使用 keys／values。异步变换要求 aiter／anext 来源，包括 Channel 和用户异步协议；不会自动把 List 转成异步流，转换须写 from_sync。flat_map 的回调结果须为同步可迭代值，aflat_map 须为异步可迭代值。

同步回调不会自动 await：map 可以产出冷 Task，filter／while 的谓词返回 Task 则不满足 Bool。异步变换对回调结果仅在其确为 Task 时 await 一次；普通结果直接使用，若完成值仍是 Task，不会递归等待。所有谓词最终都须为 Bool，不做隐式真值转换。输入、回调和错误按实际消费顺序发生，已经执行的效果不因之后失败而回滚。

```chaos example=streams-async
import "std/iter" as I;
async fn work(value) { value + 2 }
await acollect(I.atake(I.amap(work, I.from_sync([40,41,42])), 2))
```

结果为 `[42,43]`。它按需顺序等待，并不自动并发启动全部回调；并发工作仍显式采用 TaskGroup／gather。scan／ascan 在取得输入游标前输出 initial，因此空来源也有一个累积结果。

```chaos example=streams-products
import "std/iter" as I;
[collect(I.zip([1,2], ["a"])), collect(I.batches([1,2,3],2)),
 collect(I.scan(fn(sum,value){sum+value},0,[20,22]))]
```

输出对应 `[(1,"a")]`、`[(1,2),(3,)]` 和 `[0,20,42]` 三组结果。JSON 展示保留 `$tuple` 标记；它们在语言内是 Tuple。zip 第一次被推进时先从左至右取得全部来源游标，再从左至右读取一轮；即使左侧来源为空，右侧的游标工厂也已经调用。最后一轮当较右来源结束时，较左来源可能已消费本轮一项；不承诺对所有来源回退位置。chain 则只在消费抵达某个来源时才取得它的游标。

## 借用游标与显式关闭

每个来源参数位置在开始消费时取得一次游标；同一个对象作为多个参数传入时，各位置仍按调用顺序取得／推进。若它们是同一个真实游标，就共享推进位置，之后从当前位置继续。派生流借用来源，不拥有它的关闭责任。派生流暂停后，可以显式推进上游；回调显式推进同一上游也会影响下次读取的位置。不会把一个跨越 yield 的整段迭代 busy 锁挂在上游；派生流自身的递归推进仍由原生成器 busy 规则拒绝。

```chaos example=streams-borrowed
import "std/iter" as I;
import "std/context" as C;
var events = [];
fn* source() { try { yield 1; yield 2; yield 3 } finally { events = append(events,"closed") } }
let cursor = source();
let result = with C.closing(cursor) as owned {
  let prefix = I.take(owned,1);
  let first = collect(prefix);
  close(prefix);
  [first,next(owned).value,events]
};
[result,events]
```

结果为 `[[[1],2,[]],["closed"]]`。提前停止、回调出错或关闭派生流，不隐式关闭上游。需要确定清理时用 closing／aclosing 包住自己拥有的原始生成器或游标；多层来源可使用已有资源栈。close／aclose 针对指定句柄，send／throw_into 等操作也不会自动转发到上游。通道的消费游标关闭仍不关闭共享 Channel，生产者负责 channel_close。

```chaos example=streams-channel
import "std/iter" as I;
let messages = channel(2);
await channel_send(messages,40);
await channel_send(messages,41);
channel_close(messages);
await acollect(I.amap(fn(value){value+2},messages))
```

结果为 `[42,43]`。这里没有宿主线程流处理器；等待、背压、任务上下文、取消和录制范围来自原语言调度器。Team 角色及 AgentTool 函数可作为回调，授权继续与真实调用能力相交；std/swarm 的 frames 也可作为异步来源，状态仍遵守原有限 JSON 快照合同。

## 预算、工具链与嵌入

流只保存当前位置、累积值或当前批次等必要状态，batches 不把无限来源全部装入内存；累积值和回调仍可能自行增长。步数、时间、整数、容器、Task／Generator 和效果预算照常生效。collect／acollect／全局高阶消费保留各自的有界收集规则；count／repeat 虽可不设有限长度，实际执行不具有无限资源。没有隐式预取、后台任务、持久化暂停栈或缓存重放。

源码位于 `chaos/stdlib/iter.chaos`，通过现有 ProgramBundle 导入并保留 `std/iter` 的源文件身份。IDE／LSP 直接读取真实导出函数的参数和返回注解，不另写签名表，也不执行流来猜元素类型。宏、源码／IR、格式化、CLI、HTTP、SDK 与调试器使用同一 AST；页面仍是原简洁工作台。实际元素保持 Any，不把生成器最终返回契约当作每次产出类型。

标准库源码、指南和[可运行示例](../examples/lazy-streams.chaos)随同 wheel／portable 包交付。在其他工程中按[安装指南](INSTALLATION.md)配置导入根目录，即可通过 Framework.evaluate 或 run_chaos.py 执行，无需 Web 服务和模型凭据；只有回调显式调用已授权模型时才需要相应配置。

组合思想参考 [Python itertools](https://docs.python.org/3/library/itertools.html)；[Racket streams](https://docs.racket-lang.org/reference/streams.html)还包含可重访的流值与延迟求值约定。Chaos 此处采用借用的单次 Generator／AsyncGenerator，不声称兼容全部 itertools、Lisp 流或提供 memoization、tee、groupby、窗口、并发 map 等未列接口。
