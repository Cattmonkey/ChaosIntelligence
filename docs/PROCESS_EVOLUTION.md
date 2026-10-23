# Process 原子自我演进

0.45 将“修改自身程序，并保留下一轮状态”实现为语言返回协议。`process_evolve` 构造受当前 Process 轮次拥有的 `ProcessEvolution` 值；将它作为导出函数的最终结果返回，宿主才会验证候选，并将源码、历史、状态、输出、邮箱确认和运行终态一起提交。

这是持久语言函数的能力，不需要另建网页工作流。Agent 或 Team 可以提出候选；普通语言函数明确决定测试用例、状态迁移及是否继续。提示词和自然语言中的“成功”不会触发提交。

## 原生返回值

```chaos example=process-evolution-function
fn evolve(base:Program,next:Program,cases:List,state)->ProcessEvolution {
  process_evolve(base,next,cases,state,reason="checked transition")
}
type_of(evolve)
```

函数签名为 `process_evolve(base,candidate,cases,state,output=null,done=false,reason="") -> ProcessEvolution`。它是同步构造函数，不返回 Task，不在调用处运行测试或修改源码。构造需要真实 Process 执行上下文；普通求值、候选测试或伪造 JSON 不能取得此能力。

`base` 使用本轮 `current_program()`；`candidate` 是编辑或导入得到的 Program。`cases` 使用与 `program_probe` 相同的 1–16 项测试，支持[显式智能体 fixtures](AGENT_TESTING.md)。state/output 必须是原有 Process 限制内的普通 JSON；done 必须为 Bool；reason 最多 1000 字符。构造时冻结候选、用例与数据，后续修改原列表或记录不改变该决定。

`process_evolution_info(value)` 返回隔离的检查数据：`base`、`definitionHash`、`casesHash`、`testCount`、`state`、`output`、`done`、`reason`。它不返回私有轮次租约或权限，也不能从这些数据恢复可提交的值。

## 从函数返回到事务

一个 step 可以这样组织：

```chaos
async fn step(ctx) {
  let base=current_program();
  let definition=program_info(base);
  let files=set(definition.files,"behavior.chaos",
    "fn answer(x:Int)->Int{x+2};export {answer};");
  let candidate=program_from(merge(definition,{files:files}));
  let cases=[{entry:"tests.chaos",input:40,expect:42}];
  process_evolve(base,candidate,cases,
    {generation:ctx.state.generation+1},
    {executedVersion:ctx.programVersion},
    reason="new behavior and generation checkpoint")
}
export {step};
```

`tests.chaos` 可直接导入并调用新的 behavior 函数。返回决定后，解释器完成 Task、生成器和资源作用域清理，宿主关闭本轮资源，再以同一预算和取消信号运行候选测试。测试成功不等于已经提交；最终事务还会检查轮次身份、源码版本及哈希、当前权限和 Process 定义。

只有直接返回的一个决定会被采用。创建后丢弃的值没有效果；把它嵌在普通 Record、state/output、Agent 工具结果或另一个程序中不能提交。普通 `process_step` 仍返回原来的 `{state,output,done}`；`program_adopt` 仍是原来的立即采用操作。

## 失败、并发与恢复

清理失败、测试失败、预算耗尽、提交前被接受的取消、版本冲突或事务写入错误都不会部分提交这次演进。原检查点和待处理消息保留，Process 按既有语义进入 `needs_reconciliation`。源码历史、采用事件、状态和消息确认中的任一写入失败，都会回滚同一成功事务。

邮箱新增消息和暂停不使决定失效；最终提交保留已排队消息及最新暂停意图。Process 定义哈希在运行期间发生变化且提交时仍不同于启动快照时，本轮演进会拒绝提交，需要重新核对。候选仍须包含 Process 指定的模块和显式导出。这是静态链接检查，不执行模块初始化，也不证明导出值可调用或下一轮必定成功；候选 cases 应验证导出函数的实际行为。

返回值捕获创建时的权限范围，最终再与启动权限上限和当前授权求交。较窄的智能体回调、延迟函数或录制范围不能把决定交回较宽的父调用来扩大权限；JSON 字段不能赋予权限。项目需要 `process.manage` 和 `program.adopt`。

这项事务覆盖同一项目数据库中的程序与 Process 数据。已发生的网络、电脑、文件、模型会话或其他外部效果仍需按真实回执协调；本功能没有声称跨外部世界回滚，也不迁移活动调用栈。重启后新一轮加载已提交源码与状态；不恢复旧闭包。

## 查看同一份结果

Process 的可选 `lastEvolution` 保存真实采用回执：`previousVersion`、`version`、前后定义哈希、casesHash、testCount、runId 和 reason。`lastVersion` 仍表示最近一轮实际执行的源码版本。比如执行 v1 的 step 成功采用 v2，界面会分别显示这两个事实；以后普通 step 不会伪造一次新采用。

Framework／Client 的现有 `get_process`、`processes`，HTTP Process 读取、CLI `process get` 和网页持续进程面板都读取这一记录。没有额外的前端提交接口。源码、IR、编辑器提示与无界面解释器沿用同一语言实现。

## 行为配置学习与源码演进

如果只需要改进提示、操作策略或经验，先使用[std/evolve](HARNESS_EVOLUTION.md)，把成功后的 snapshot 通过普通 `process_step` 保存。只有实际改变项目模块时，才把候选转换为 Program，提供独立测试并使用本页的 `process_evolve`。对比执行所得的模型判断不能代替源码采用的验证，保存经验也不会默认授予 `program.adopt`。

## 完整 dot 示例

[启动程序](../examples/evolving-dot/main.chaos)、[多角色持续函数](../examples/evolving-dot/dot.chaos)、[行为模块](../examples/evolving-dot/behavior.chaos)和[验收入口](../examples/evolving-dot/tests.chaos)组成可运行包。编程角色提出完整 Program，审查角色返回明确 JSON，语言执行测试并原子提交行为模块和 generation 状态。当前函数继续使用旧快照，下一轮使用新版本。

输入支持 `provider`、`key`、`value`、`iterations`、`intervalSeconds`；把 intervalSeconds 设为 null 可通过持久消息驱动下一轮。生产 provider 使用系统已有配置；测试使用明确的受控 Provider，不代表真实模型的优化成功率。回归覆盖两次采用、宿主重启及第三轮执行。

相关指南：[持续函数](PROCESSES.md)、[自编程](SELF_PROGRAMMING.md)、[智能体协议测试](AGENT_TESTING.md)、[程序历史](PROGRAM_HISTORY.md)。
