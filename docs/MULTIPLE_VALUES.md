# 显式多返回值

一次语言调用可以完成零个、一个或多个结果。`return_values(*items)` 产生多结果完成；普通绑定、参数、容器元素、条件和对象协议只使用首项，零结果的首项为 `null`。函数返回注解检查首项，其他结果仍经过所有权、权限与资源预算检查。结果不是 Tuple 或可放入普通容器的隐藏对象。

全局 `values(obj)` 保持原有 Record 值提取接口。`import "std/values" as V` 后，`V.values` 与 `V.return_values` 是同一个 `return_values` Native 的别名。

```chaos example=multiple-primary
[values({answer:42}),return_values(42,"trace"),return_values()]
```

## 捕获与投影

`multiple_value_list(producer)` 接收零参 callable，或本轮已完成的 Task／Generator／AsyncGenerator，并捕获全部结果；`nth_value(index, producer)` 取从零开始的一项，数量不足时返回 `null`。`index` 必须是非负 Int。先求值再放入普通参数会丢弃次项，因此捕获接口接收真实 producer 函数或显式完成的原生句柄。已完成句柄只读终值；冷、运行中、产出后暂停、失败、取消或已关闭的句柄被拒绝，不隐式 await、推进或重放。外来或过期句柄继续受原所有权／生命周期检查。

```chaos example=multiple-capture
import "std/values" as V;
[V.multiple_value_list(fn(){V.values()}),
 V.multiple_value_list(fn(){V.values(null)}),
 V.multiple_value_list(fn(){[20,22]}),
 V.multiple_value_list(fn(){(20,22)})]
```

零结果捕获为 `[]`，单个 null 为 `[null]`；一个 List 或 Tuple 仍是一个结果。Tuple 沿已有公开 JSON 投影显示为 `$tuple` 记录。多结果自身没有 JSON 标记，顶层 SDK／CLI／HTTP 输出仍取首项；需要发送全部结果时先捕获成 List。

## 调用、绑定与清理

| 接口 | 执行合同 |
| --- | --- |
| `return_values(*items)` | 各实参先取首项，再产生相应数量的结果 |
| `values_list(items)` | 有界 List 的各项成为结果；空 List 产生零结果 |
| `multiple_value_list(producer)` | 读取 producer 全部结果，返回 List |
| `nth_value(index, producer)` | 读取 producer 指定结果或 null |
| `multiple_value_call(function, *producers)` | 从左到右读取 producers，连接全部结果并传给 function |
| `multiple_value_bind(count, producer, consumer)` | 从 producer 取 count 项调用 consumer；不足补 null，多余舍弃 |
| `multiple_value_prog1(first, *afters)` | 保存 first 全部结果，顺序读取 afters，再恢复结果 |

表中的 producer、first、afters 和 producers 接受零参 callable 或精确的已完成 Task／Generator／AsyncGenerator；function 与 consumer 始终是实际 callable。纯宏阶段只接受纯函数回调，没有运行时句柄。

`count` 必须是非负 Int；结果、展开参数和捕获 List 都受现有 10000 项上限与执行预算约束。回调错误立即传播，已经发生的外部效果不回滚。普通函数尾调用、return、分支、块、受保护结果与 finally 保留完整完成结果；布尔短路继续产生一个严格 Bool 首项。

```chaos example=multiple-composition
var events=[];
let total=multiple_value_call(fn(a:Int,b:Int)->Int{a+b},fn(){return_values(20,22)});
let report=multiple_value_list(fn(){
 multiple_value_bind(3,fn(){return_values(total,"ok")},fn(value,status,missing){return_values(value,status,missing)})
});
let kept=multiple_value_list(fn(){multiple_value_prog1(fn(){values_list(report)},fn(){events=append(events,"after")})});
[kept,events]
```

## 异步、工具与纯宏

这些接口在当前逻辑调用中进入显式 `async fn` 回调，因此异步 producer 可以使用 `await` 捕获真实 Task 的完整完成。普通回调返回的冷 Task／Generator 仍是一个结果；它不会因此自动启动。gather 的结果列表和生成器的 next 记录沿原数据合同使用首项，`yield from` 可传递委托生成器的完整终值。

```chaos example=multiple-async
var calls=0;
async fn work()->Int{calls+=1;await sleep(0);return_values(42,"task")}
fn* stream()->Int{calls+=1;yield 7;return_values(42,"generator")}
async fn* async_stream()->Int{calls+=1;yield 8;return_values(42,"async-generator")}
let task=work();let awaited=multiple_value_list(async fn(){await task});
let generator=stream();next(generator);let done=next(generator);
let async_generator=async_stream();await anext(async_generator);let async_done=await anext(async_generator);
[awaited,multiple_value_list(task),multiple_value_list(generator),multiple_value_list(async_generator),calls,done.value,async_done.value]
```

Team 的活函数角色 Task 保留完整完成；AgentTool 的外部输出合同和提供方收到首项。工具需要输出全部结果时，处理函数显式返回 `multiple_value_list(...)`。调试、事件与持久 JSON 不导出内部完成身份。

纯宏支持相同七个函数和纯 producer 回调合同，不接受运行时完成句柄；回调保持纯编译期函数，不能获得运行时 Agent、Task 或外部效果。quote／unquote、Code、Program、源码与结构编辑仍使用既有调用 AST。IDE／LSP 对静态首项和已声明回调返回类型提供保守提示，不执行 producer。

本合同参考 [Common Lisp values](https://www.lispworks.com/documentation/HyperSpec/Body/f_values.htm)、[多值求值规则](https://www.lispworks.com/documentation/HyperSpec/Body/03_ababc.htm)、[multiple-value-call](https://www.lispworks.com/documentation/HyperSpec/Body/s_multip.htm)、[multiple-value-bind](https://www.lispworks.com/documentation/HyperSpec/Body/m_multip.htm) 和 [multiple-value-prog1](https://www.lispworks.com/documentation/HyperSpec/Body/s_mult_1.htm)。Chaos 使用上表中的显式函数回调接口，不声称逐字兼容 Common Lisp special forms 或完整 Python／Lisp 生态。

完整源码示例见[多返回值程序](../examples/multiple-values.chaos)。

## 0.135 候选：真实 receiver／producer 状态

五种 receiver 操作与两种 producer 来源由同一逻辑执行者上的两个真实算法帧推进：MultipleValueResolveFrame 保存操作、producer 顺序、当前索引、完整结果与参数草稿；MultipleValueReceiveFrame 进入原 force／context／all-results 调用，或读取原已完成 Task／Generator／AsyncGenerator 的终值并按原能力投影。公开入口不再保存整段 receiver／producer 生成器算法；MultipleValuesCloseCursor 只运输原关闭前沿，不另建 Runner 或重复效果。

零／null／多结果、普通首项投影、左到右求值、参数预算、次项所有权、call 的原 TailCall 与 bind 的原强制上下文调用保持不变。普通回调返回冷句柄仍是一个值；未完成句柄仍被拒绝，不隐式推进。纯宏使用原纯阶段合同，不借此获得运行期帧或权限。源码／IR、Code／Program、显式编译调用与 portable artifact 加载、AgentTool／Team／Bot、软件 Robot 和 SDK／CLI／HTTP 共用原求值器。算法帧合计124种，公开原语仍333个。

这是内部待决状态实现，不是通用 continuation、活栈持久化或提速承诺；源码、实测与安装分别验收。后续控制段、wind 再进入与线性屏障的具体合同见[continuation 设计](CONTROL_PROMPTS.md#continuation-design)。
