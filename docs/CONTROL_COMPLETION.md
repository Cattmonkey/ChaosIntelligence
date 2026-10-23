# 正常完成分支与智能体控制流

循环的 `else` 表示正常耗尽后的动作，`try` 的 `else` 表示受保护计算成功后的动作。两者都是语言原生表达式，由解析器、解释器、宏及 Program IR 共同定义。它们可以包含普通函数、Agent 调用、显式 await 或工具效果，执行仍使用当前权限、预算和调度器。

## 循环耗尽与表达式结果

`while condition { body } else { otherwise }` 在条件变成 false 时运行 otherwise，包括第一次判断就为 false。`for target in source { body } else { otherwise }` 在迭代器报告耗尽时运行 otherwise，包括空来源。`async for` 使用相同规则，显式等待异步来源。

```chaos example=completion-search
fn find(items){
  for value in items {
    if value>20 {return value}else{null}
  } else {42}
}
[find([]),find([10,21]),while false{}else{42},
 for value in [1]{break}else{99}]
```

结果为 `[42,21,42,null]`。else 块实际执行时，其最后一个表达式就是整个构造的结果；空块产生 null。break 跳过本循环的 else 并返回 null。没有 else 的旧循环仍返回 null，不因本次扩展改变。

continue 只跳过当前轮，之后仍可能耗尽。return、向外传播的错误、生成器关闭和取消不会伪装成耗尽。内层循环的 break 只结束内层循环，不会自动阻止外层循环的正常完成分支。来源、迭代器获取／推进、模式绑定或 while 条件中的控制信号保留原有目标；本循环只处理其 body 边界原本能够处理的 break／continue。

## 作用域与嵌套控制

循环目标可以是名称或结构模式，每轮创建新的局部绑定，仅在该轮 body 内可见；else 是从外层环境建立的另一个块，不能读取循环目标、body 局部变量或 catch 的错误绑定。外层已有的名字和可变单元按普通词法规则可见。

```chaos example=completion-scopes
let item=42;
let error=7;
[for item in []{}else{item},
 try{let hidden=1;20}catch error{0}else{error}]
```

结果为 `[42,7]`。两个 else 读取的都是外层名字。格式器、变量重命名和提示使用同一作用域，不会把名字相同的迭代变量或 catch 变量当作它们的定义。

循环 else 已经在本循环的控制范围之外。其中的 break／continue 可以控制实际包围它的外层循环；没有外层循环时属于语法错误。函数、推导式和宏展开仍检查各自的控制边界，不能靠包装一个 else 穿越边界。

## finally 后的最终控制信号

循环依据真正到达它的控制信号决定是否正常完成。finally 把 break 替换为 continue 后，循环可以继续并正常耗尽；反方向的替换则抑制 else。原有清理语义没有增加额外预算。

```chaos example=completion-cleanup-control
var turn=0;
let result=while turn<2 {
  turn=turn+1;
  try{break}finally{continue}
}else{42};
[result,turn]
```

结果为 `[42,2]`。资源耗尽和已交付的硬取消继续直接传播，不执行成功分支，也不能通过 finally 延长运行。借用迭代器、生成器关闭及任务组清理继续遵守[迭代器](ITERATORS.md)、[资源作用域](CONTEXTS.md)和[取消语义](CANCELLATION.md)。

## try 成功分支与错误边界

语法为 `try { body } catch error { handler } else { otherwise }`，可继续接 `finally { cleanup }`。else 必须有前面的 catch（0.55 也支持有序模式 catch）；没有新增 `try { ... } else { ... }` 形式。只有 body 正常完成才运行 else；handler 即使正常返回，也不会再执行 else。

```chaos example=completion-try-boundary
var events=[];
let result=try{
  try{events=append(events,"work");20}
  catch error{events=append(events,"fallback");0}
  else{throw "commit"}
  finally{events=append(events,"cleanup")}
}catch error{error.value};
[result,events]
```

结果为 `["commit",["work","cleanup"]]`。成功处理失败后，错误直接交给外层处理器；同级 catch 不会把它解释成原始计算失败。finally 包含 body、handler 和 else 的退出路径，其普通结果仍被丢弃。

有 else 时，body 成功值被丢弃，表达式结果来自 else；出错时仍来自 handler。body、handler、else、cleanup 各自保留独立块作用域。body 的尾调用在受保护范围内完成；显式 return 不触发 else。保护体最后一项求值得到冷 Task（未执行 return）属于普通完成，不隐式等待；需要把异步失败纳入 catch 时，在 body 中写 `await`。

## 异步来源与智能体函数

```chaos example=completion-channel
let messages=channel(2);
await channel_send(messages,20);
await channel_send(messages,22);
channel_close(messages);
var total=0;
async for item in messages {total=total+item}else{total}
```

结果为 `42`。Channel 关闭并排空后会正常耗尽；break、取消、死锁或异步推进失败不等价于耗尽。else 内可以继续 await，挂起期间其他任务照常由同一调度器推进。

```chaos example=completion-functions
fn choose(candidates,score,otherwise){
  for candidate in candidates {
    if score(candidate){return candidate}else{null}
  }else{otherwise()}
}
choose((20,21),fn(value){value==42},fn(){42})
```

结果为 `42`。项目中的 score、otherwise 可以来自普通闭包、Agent 或绑定的 Team 角色；异步角色仍需显式等待。AgentTool／AgentSession 工具处理器可在函数内部使用这组控制流，模型协议、JSON 边界和调用权限保持不变。外部效果不会因为后续分支失败而自动回滚。

## 宏与 Program 自修改

```chaos example=completion-macro
macro finish(work,done){quote{
  try{unquote(work)()}catch error{error.value}
  else{unquote(done)()}
}}
finish!(fn(){20},fn(){42})
```

结果为 `42`。quote 中的循环目标和 catch 名称仍卫生重命名，else 使用同一个外层词法环境。展开后会再次验证控制目标和异步位置。纯编译期求值器仍采用其明确支持的子集；这些运行期循环／异常形式须放在 quote 中。

```chaos example=completion-program
let old=program_from({kind:"program",entry:"main.chaos",
  files:{"main.chaos":"for item in []{item}else{41}"},
  languageVersions:{"main.chaos":2}});
let branch=program_ir(old).root.statements[0].otherwise.statements[0];
let changed=program_edit(old,"main.chaos",[
  {op:"set",id:branch.id,field:"value",value:42}
]);
[branch.value,program_ir(changed).root.statements[0].otherwise.statements[0].value]
```

结果为 `[41,42]`。原 Program 保持不变；实际执行和采用仍需项目宿主及原有 probe/adopt 验证。智能体能够用这套结构机制修改自己的耗尽／成功策略，无须从自然语言关键词猜测程序。

## 统一表示与实现边界

`chaos.program/1` 增加 `loop_else {loop,otherwise}` 与 `try_else {body,otherwise}`。loop 字段只能是现有五种循环节点；try_else 的 body 必须是原 try/catch 或有序 try_cases 节点；otherwise 必须为 block。finally 的 body 还可以是 try_else。旧节点的字段、语义哈希和无 else 行为保持原合同。

源码、CLI、SDK、HTTP、LSP、IDE 及结构编辑器共享这些节点。编辑器可以修改完成块、限制合法控制结构并同步外部保存；高亮、提示、格式化和调试定位来自实际语法与词法环境。静态提示遇到不同路径的不同结果时保守返回 Any，不执行 Agent 或工具推测类型。

0.55 的[异常扩展](EXCEPTIONS.md)补充了多 catch、结构模式、守卫及裸重抛；异常类继承、活动栈迁移和完整 Python/Lisp 生态兼容仍未提供。语义借鉴 [Python 复合控制语句](https://docs.python.org/3/reference/compound_stmts.html)，表达式结果、块作用域、显式 Task 与资源预算遵循上述 Chaos 合同。
