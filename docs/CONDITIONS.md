# 情况处理与恢复出口

0.71 将 Condition、动态处理器和恢复出口放入语言运行时。普通函数、智能体、AgentTool、Team 角色和 Program 都通过原 AST、函数绑定器与调度器组合它们；页面不根据错误文字、函数名或提示词推测恢复意图。

情况描述与恢复动作分开：低层函数报告结构化情况，高层策略选择当前程序明确提供的恢复出口。处理器在原调用现场运行，能够读取仍然活动的出口。`restart_case` 的出口则真正展开内层调用和资源范围，之后执行恢复函数。

这是 Chaos 的原生动态恢复合同，参考 [Common Lisp 条件系统](https://www.lispworks.com/documentation/HyperSpec/Body/09_a.htm)、[handler-bind](https://www.lispworks.com/documentation/HyperSpec/Body/m_handle.htm)、[restart-bind](https://www.lispworks.com/documentation/HyperSpec/Body/m_rst_bi.htm) 和 [restart-case](https://www.lispworks.com/documentation/HyperSpec/Body/m_rst_ca.htm)。语言提供下面的显式交互式恢复调试；inline 恢复返回值采用 Chaos 自己的合同。它不表示已经实现 Common Lisp 全部条件类层次或整个 Python／Lisp 生态。

## 不可变情况数据

```chaos example=condition-data
let missing:Condition=condition("missing-input","需要输入",{fallback:41});
let payload=getattr(missing,"data");
[type_of(missing),missing.kind,missing.message,payload.fallback]
```

结果为 `["Condition","missing-input","需要输入",41]`。`condition(kind,message="",data=null)` 接受用户明确提供的类别，不保留任何特别错误关键词。kind 是 1–160 字符 String，message 最多 2000 字符。

data 可以是 Null、Bool、有界 Int、有限 Number、String、Fraction、Complex、Decimal、List、Record、Tuple、Set 和嵌套 Condition。构造时复制为封闭不可变数据树，每次成员读取或 `condition_info` 返回新的容器副本；不能携带函数、类、实例、Task、Computer、上下文或其他活能力。精确数值保留其原生类型和 Decimal quantum。

data 最多 20000 个节点、64 层、累计 250000 个字符，每个容器最多 10000 项；嵌套 Condition 的 kind/message 也计入同一数据预算。顶层 kind/message 分别受上述独立长度上限约束。循环数据和宿主子类拒绝进入此原生值。

`kind`、`message`、`data` 只读。因为 `data` 是语言声明保留词，读取成员采用原有 `getattr(c,"data")` 或 `c.["data"]`；这遵循已有通用成员语法。普通方括号条目协议不自动为 Condition 建立新的容器语义。

`condition_info(c)` 返回 `{kind,message,data}`。Condition 按类别、消息和类型保持的数据内容相等；`1` 与 `true` 不混为相同数据。`json_stringify`／`str` 输出 `$condition` 描述，普通 JSON 解析仍返回 Record，不自动恢复 Condition 或任何恢复权限。

## 动态处理器

```chaos example=condition-handler
var seen=[];
with handler_bind([{predicate:fn(c){c.kind=="notice"},
                    handler:fn(c){seen=append(seen,c.message)}}]) as policy {
  signal_condition(condition("notice","已检查"));
}
seen
```

结果为 `["已检查"]`。`handler_bind(bindings)` 返回一次性 HandlerScope，使用同步 `with ... as ...` 安装。每项严格为 `{predicate,handler}`，两者都是真实语言可调用值，接收一个 Condition。

搜索由内层范围到外层范围，每组按声明次序测试。predicate 必须返回严格 Bool；第一个匹配处理器被调用。处理器正常返回意味着拒绝处理，返回值忽略，搜索继续到外层组，同组后续处理器不再调用。这是明确的 Chaos 分组规则，不宣称逐字复制 Common Lisp 同组拒绝搜索行为。

测试及处理期间，当时当前组和更内组被暂时屏蔽；处理器重新 signal 不会递归调用自身。但处理器内部新安装的组正常可见。处理器错误向外传播，屏蔽状态通过原生 finally 恢复。

`signal_condition(c)` 全部处理器拒绝时返回 Null。`error_condition(c)` 使用同一处理流程；默认若没有发生恢复转移，则抛普通 `condition_error`，其 value 是 `condition_info(c)` 的纯数据记录。明确开启情况暂停时，还可通过[交互式恢复](#在原调用现场交互式恢复)返回选择的 inline 结果。

旧 `throw`／`catch`、重抛、Decimal 信号保持原合同。系统不自动将每个旧错误改为情况；ResourceLimit、ExecutionControl、宿主中断和硬取消也不能通过情况策略恢复。

## 原地恢复与展开恢复

```chaos example=condition-inline
with restart_bind([{name:"increment",function:fn(x:Int){x+1}}]) as recoveries {
  [invoke_restart("increment",[41]),invoke_restart("increment",[9])]
}
```

结果为 `[42,10]`。`restart_bind` 提供 inline 出口：调用实际恢复函数后，返回原 `invoke_restart` 的位置。

```chaos example=condition-case
var events=[];
let answer=restart_case(fn(){
  with handler_bind([{predicate:fn(c){c.kind=="missing"},handler:fn(c){
    invoke_restart("use-value",[getattr(c,"data").fallback]);
  }}]) as policy {
    try { error_condition(condition("missing",data={fallback:41})) }
    finally { events=append(events,"cleanup") }
  }
},[{name:"use-value",function:fn(value:Int){
  events=append(events,"restart");value+1
}}]);
[answer,events]
```

结果为 `[42,["cleanup","restart"]]`。`restart_case(body,bindings)` 调用 body；正常完成返回其结果。选择 case 出口后，私有 RestartTransfer 穿过普通 catch、深层函数及尾调用，跳过尚未执行的后续代码，并完成内层 finally／with／ExitStack 清理。目标 case 已经退出，才调用其恢复函数；此时目标出口不再活动。

TaskGroup 的透明组体属于同一动态调用范围。转移仍要等待自有成员完成／排空；真正子成员没有父出口权限。成员失败、取消和预算控制不能被清理中的恢复选择变成成功。普通清理错误可以替换此前普通恢复转移；内层清理选择其他有效出口时也遵循真实展开。

恢复不是事务：已经完成的模型请求、工具操作和存储效果不会回滚，不会自动重试或回放整个 body。重试应由语言函数／循环明确编写。

## 出口合同与查询

bindings 每项包含 `name`（非空 String 或 Null）、`function`（原语言可调用值），以及可选 `report` String 和 `test` 可调用值。report 默认具名名称或空字符串，只是描述；模型不能通过描述获得回调或权限。

```chaos example=condition-query
with restart_bind([
  {name:"use",function:fn(value,/,*,bonus=2){value+bonus},report:"提供替代输入"},
  {name:null,function:fn(){0}}
]) as scope {
  let handles=compute_restarts();
  [restart_info(handles[0]).name,invoke_restart(handles[0],[40],{bonus:2}),
   invoke_restart(handles[1])]
}
```

结果为 `["use",42,0]`。同名出口允许存在，顺序由内到外、同组按声明顺序；`find_restart(name,condition=null)` 返回首个可见真实 Restart 或 Null。匿名出口通过 `compute_restarts(condition=null)` 的真实句柄选择。

可选 test 接收 Condition 或 Null，必须返回严格 Bool。compute／find 是明确运行时操作，会执行 test；`restart_info`、成员读取、JSON、文本和调试预览不执行 test、处理器或恢复函数。

`invoke_restart(restart,args=[],kwargs={})` 接受当前活动 Restart 或具名 String。实参由原绑定器验证，保留位置专用、默认值、仅关键字、结构模式和返回契约。传入实际句柄时选择其精确身份；不重新用名称换成另一个出口。

HandlerScope、RestartScope 和 Restart 属于当前 scheduler／epoch。调用出口还要求当前逻辑 holder 和活动动态范围完全匹配；scope 一次性进入，退出后句柄不能调用，描述数据不能恢复句柄。每轮最多创建 10000 个 scope，每个最多 128 项，活动嵌套最多 128 层，所有搜索与回调受原步骤和时间预算限制。

## 异步、生成器与权限

```chaos example=condition-async
async fn child(){find_restart("use")}
restart_case(async fn(){
  let independent=await child();
  await sleep(0);
  invoke_restart("use",[independent]);
},[{name:"use",function:async fn(value){await sleep(0);[value,42]}}])
```

结果为 `[null,42]`。情况系统的原生高阶回调入口透明执行直接语言 async 函数或绑定方法，复用原 `_s_invoke`、参数／返回契约、frames 和当前逻辑 holder。内部 guard 在首次真实 Function 入口消耗；回调函数体内的普通 async 调用仍创建真正独立冷 Task，必须显式 await。

这不是全局自动 await。其他可调用包装器保持其原 TailCall／continuation 行为；返回冷 Task／Generator 的 handler 报 `condition_protocol_error`，应在语言函数中显式 await 或消费。body／case／inline 恢复函数可以有意返回冷值，退出范围后交给调用者。生成器回调不能强制进入普通 runner，即使经过 partial 等包装；实际生成器消费仍走原迭代协议。

真实 Task、同步／异步 Generator 创建空动态恢复 holder，兄弟任务互不继承活处理器和出口。Generator 在 yield／send／throw／await／close 间保留自身 holder，恢复时优先使用它；调用者不能调用暂停生成器的出口。透明 drive、TaskGroup 私有 body 和仅为资源协议新建的内部 Task 共享调用方 holder；借用／已经创建的用户 Task 不被改写。关闭及硬中断始终释放原生 scope 记录，即使任意用户清理被跳过。

出口本身不授予工具权限。注册处、调用处、当前执行处和回调自身能力取交集。case 转移在展开之前捕获调用处能力，因此受限 AgentTool 选择外层出口也不能因退出受限函数而获得更宽权限。

智能体可以接收 `condition_info` 和 `map(restart_info,compute_restarts(c))` 的纯结构数据，返回出口名字和显式参数，再由原语言任务验证并调用实际出口。BoundAgent 返回冷 Task；用 async 函数显式 await 决策，再在同一回调范围 `invoke_restart`。不让模型线程或独立子 Task 直接跳转父栈。

## 在原调用现场交互式恢复

`invoke_debugger(c)` 接收真实 Condition，在启用调试的当前语言执行中暂停。也可在 `start_debug(..., pause_on_condition=True)` 或 debug 配置 `pauseOnCondition: true` 下，让未处理的 `error_condition(c)` 在所有 handler 正常拒绝后进入同一暂停。默认 false 保留原错误行为；普通 `signal_condition`、throw 与硬取消不自动进入恢复调试。没有可用调试器时显式调用抛 `condition_error`。

```chaos
restart_case(fn() {
  error_condition(condition("missing-value", "需要替代输入"))
}, [{name: "use-value", report: "使用提供的整数", function: fn(value: Int) { value + 1 }}])
```

调试入口在原解释器线程明确执行 `compute_restarts(c)` 的 test，冻结当前 holder 中可见出口的精确身份，然后发布有界数据。HTTP 读取快照不再次运行 test，不展示函数或活 Restart。具名重复出口与匿名出口都按 id 选择；截断状态会公开，不能用未展示的 id 调用任意出口。test 准备期间重入恢复调试明确拒绝，guard 在 finally 中恢复。

选择 case 出口仍通过原 RestartTransfer 展开，完成内层 finally／with 清理后进入恢复函数。选择 inline 出口将该恢复函数的正常结果作为 `invoke_debugger` 或这次 `error_condition` 的返回值，包括 Null；不会重放失败函数。此返回规则是 Chaos 扩展，区别于 Common Lisp invoke-debugger 不直接返回的标准语义。

继续或单步命令表示拒绝这次恢复，随后传播 `condition_error`；停止执行原取消规则。恢复出口不增权，不把失败前工具效果回滚，也不能把超时、预算或取消恢复成成功。真正子 Task 不能选择父任务出口；Program／Team／AgentTool 里的暂停仍使用其实际 holder 与能力交集。完整操作和无界面命令见[调试器](DEBUGGER.md)。

## 宏、IDE 与部署

```chaos example=condition-macro
macro missing(){syntax_literal(condition("missing",data={value:decimal("0.10")}))}
let condition=fn(kind){42};
let value=missing!();
[value.kind,decimal_text(getattr(value,"data").value)]
```

结果为 `["missing","0.10"]`。纯宏阶段支持情况数据和 condition_info，syntax_literal 使用编译器拥有的封闭构造器身份，调用方同名绑定不会截获原生构造器。动态 handler／restart API 不在纯宏阶段提供。

标准模块 `std/conditions` 导出本页全部原生 API。IDE／LSP 从共享 NativeSpec 提供高亮、签名、只读属性和补全；分析与格式化不执行用户回调。源码和 IR 仍使用已有 call／with／函数节点，Program 候选编辑与可视化图修改同一源码快照；CLI、Framework、SDK 和 HTTP 使用同一解释器。

恢复回调是运行时控制／效果边界。现有 LLVM 整数区域和 GPU 数值内核不会编译处理器、活出口或模型调用；分析保持保守，auto 使用原解释路径，严格目标报告不支持。没有通过本增量宣称通用 JIT、AOT、自研 GC 或活栈持久化完成。

运行完整示例：`python -m chaos run examples/conditions.chaos`。安装、签名远程更新与 APIv1／IR 版本保持原合同，参见[安装部署](INSTALLATION.md)、[SDK](SDK.md)、[开发者指南](DEVELOPER.md)及[能力对照](PYTHON_LISP_CAPABILITIES.md)。

[显式多返回值](MULTIPLE_VALUES.md)可捕获动态恢复与受保护主体的完整完成；首项契约、资源清理与真实展开顺序沿原恢复协议执行。

## 恢复状态与完整结果

全部恢复请求与共享回调由[原执行游标上的情况帧](EXECUTION_FRAMES.md#情况与恢复的执行状态)执行。源码、结构 IR、Code／Program、宏、智能体函数与无界面入口不需要选择新模式。处理器屏蔽、出口搜索、调试选择和 case 展开都属于实际执行状态；纯查询预览和序列化数据不能恢复活出口。

```chaos
var seen=[];
let answer=multiple_value_list(fn(){
 restart_case(fn(){
  with handler_bind([{predicate:fn(c){c.kind=="missing"},handler:fn(c){invoke_restart("use",[c.["data"].fallback])}}]) as policy{
   try{error_condition(condition("missing",data={fallback:41}))}
   finally{seen=append(seen,"cleanup")}
  }
 },[{name:"use",function:fn(value){
  seen=append(seen,"restart");return_values(value+1,find_restart("use")==null)
 }}])
});
[answer,seen]
```

结果为 `[[42,true],["cleanup","restart"]]`：清理先于恢复函数，目标 case 出口在恢复函数运行时已不活动，多结果由显式 producer 捕获。此表示不改变步骤、取消与权限合同，也不提供保存／复用 continuation。
