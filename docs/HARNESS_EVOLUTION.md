# 智能体 Harness 的持续改进

`std/evolve` 把“提出不同做法 → 实际执行比较 → 组合后再执行 → 提炼经验”写成可组合的语言函数。操作者是实际 Function、Agent 或 Team 角色，不按任务关键词选择流程。它使用原有 Task、能力与资源预算，CLI、SDK 和网页执行同一份代码。

这里的 Harness 是你交给执行函数的行为配置，例如提示、工具使用说明、记忆或子角色设置。每项新任务仍从固定 `base` 开始；跨任务保留的是改进策略和经验，避免把上一任务的特殊答案变成全局默认行为。方法参考 [Harness² 论文](https://arxiv.org/html/2610.14224v1)及[上游实现](https://github.com/google-research/harness2/tree/1cfe254e8d25b15f37048c3730134686abe60335)。

## 先运行无需密钥的示例

示例包由[入口](../examples/harness-evolution/main.chaos)、[确定性操作函数](../examples/harness-evolution/operators.chaos)、[模型操作函数](../examples/harness-evolution/model.chaos)和[持续函数](../examples/harness-evolution/process.chaos)组成。把四个文件一起保存到工程的 `harness-evolution/` 目录，保留文件名，然后运行：

```sh
python3 chaos.py run harness-evolution/main.chaos --stream
python3 chaos.py run harness-evolution/main.chaos --input-json '{"provider":"local","k":2,"mode":"sequential"}' --stream
```

在源码仓库或已安装 CLI 的环境，可用 `python3 -m chaos run examples/harness-evolution/main.chaos --stream`。Windows 终端的 JSON 引号规则可能不同，也可在 IDE 的运行输入中填写同一 JSON。

默认 `local` 使用确定性的真实函数执行，供检查流程。阶段进度包含 reference、propose、probe、compose、select、learn；最终结果有新快照、被选择的已执行结果和执行记录。`k=1` 有 4 次 probe，`k=2` 有 8 次。次数证明协议确实调用了执行函数，不证明模型质量提高。

配置好自己的模型后，将输入改为 `{"provider":"model","task":"整理一个适合新手的项目阅读方案","k":1}`。模型操作函数使用系统已有提供方与角色路由；Base URL 和密钥继续放在[私人模型配置](MODEL_CONFIGURATION.md)，不放入候选 Harness、经验或源码。真实模型调用按配置计费，内容和质量取决于模型、任务及执行环境。

## 接口与状态

```chaos example=harness-evolution-create
import "std/evolve" as E;
let initial = E.create({prompt:"先核对输入再回答"}, {focus:"可核对的结果"});
[initial.base.prompt, initial.revision, initial.lessons]
```

| 接口 | 行为 |
| --- | --- |
| `E.create(base, strategy=null)` | 校验并复制初始快照；revision 为 0，lessons 为空 |
| `await E.run(snapshot, task, operators, mode="parallel", k=1, observe=callback)` | 返回一次完整任务的报告；mode、k、observe 是仅关键字参数 |

快照恰为 `{base,strategy,lessons,revision}`。`base` 是共同起点；`strategy` 是改进操作使用的策略数据；lessons 是保留的经验；revision 每次完整成功增加 1。`run` 返回冷 Task：构造任务不执行操作，等待后才开始。

报告包含 `snapshot`、`selected`、`records`、`learning`、`mode`、`k`。selected 必须是已经执行的 composed 记录；它包含 `{id,harness,output,evidence,reason}`；reference 的 reason 为 null，候选与 composed 记录保留提案的公开理由。`report.snapshot.base` 保持共同基线，`report.selected.harness` 是本次任务选中的做法。后续任务将前者与新策略、经验一起传入 `run`；用户决定是否以及如何持久化。

## 五种操作都是函数

`operators` 必须恰好包含下表的五个可调用值。每个函数接收一个复制的数据记录；同步返回直接验证，返回冷 Task 则交给当前 TaskGroup 等待一次。不自动展开第二层任务，也不接纳外部已启动或其他组拥有的 Task。

| 操作 | 输入 | 必须返回 |
| --- | --- | --- |
| `propose` | `{scope,ordinal,task,harness,strategy,lessons,records,revision}`；scope 是 general 或 domain | `{harness,reason}` |
| `probe` | `{id,harness,task}`，候选与 composed 输入另有 reason | `{output,evidence}`：真实执行得到的结果与公开证据 |
| `compose` | 与 propose 相同的字段，scope 为 compose；records 含已经完成的执行 | `{harness,reason}`，可保持当前配置或组合不同做法 |
| `select` | `{task,outputs}`，只含 composed 最终输出 | 0 到 k−1 的 Int 索引；不能生成新的答案 |
| `learn` | `{task,selected,records,strategy,lessons,revision}`；selected 是记录 ID | `{strategy,lessons,evidence}` |

propose 与 compose 都收到任务开始时固定的策略和经验；当前任务的学习只在最后发生。reason 是公开理由，不是私有推理链。selector 不接收候选 Harness、轨迹或来源标签；输出顺序仍按生成顺序排列，没有上游的随机洗牌。`k=1` 自动选择唯一 composed 输出，不调用 select，但仍校验其可调用性。

新的 lesson 恰为 `{cue,lesson,evidence}`：cue 说明适用条件，lesson 描述可以复用的经验，evidence 是本任务执行记录的 ID 列表。改变策略或新增经验时，学习层的 evidence 及每条新经验都需引用至少两个不同且实际存在的执行 ID，例如 `reference-0` 和 `composed-0`；不存在或重复的引用会拒绝。已有快照中的经验也不能含重复引用。通过引用验证不代表程序证明了文字结论；它仍是基于观察的判断。没有可支持的变化时，直接返回收到的策略 `{strategy:c.strategy,lessons:[],evidence:[]}`，表示本次不新增经验；策略必须保持原 JSON 内容和字段顺序，不能借空引用提交修改。

探测应执行实际行为，而非由模型声称“已经测试”。可以使用普通函数、调用 `agent_from(definition)` 得到的 Agent，或在已授权宿主中调用 `run_program`。`program_probe` 则用于有明确 expect 的确定性测试；两者用途不同。对返回结构的校验使用正式数据合同，不剥离 Markdown 或猜测回答关键词。

## 并行与顺序模式

`k` 是 1–4 的 Int。两种模式均先执行 k 次共同基线，生成 `reference-0` 等记录，总计 4k 次 probe。

- **parallel**：同时提出 k 个 general 和 k 个 domain 候选，执行它们；k 次组合都读取同一个完整候选记录集合，再分别执行 composed Harness。
- **sequential**：进行 k 轮；每轮提出并执行一个 general、一个 domain 候选，组合并执行一个 successor。下一轮以刚执行的 composed Harness 为当前配置，保留此前记录；最终选择范围是各轮 composed 输出。

只有 k 个 composed 输出参与最终选择；reference 和未组合候选用于比较，不会被直接当成最终结果返回。选择或学习失败时，本次 run 失败，不把“取第一个结果”伪装成成功。候选函数仍可以通过返回原配置表示保持原行为。

所有探测都共享当前运行的能力和预算；并行不等于环境隔离。访问真实文件、模型、网络、设备的执行函数可能产生外部效果，失败不会撤销它们。给机器人／键鼠改进行为时，先提供可复位模拟器、只读观察或显式处理重复操作的执行函数；这个标准模块不会自动创建设备沙箱。

## 实时展示与用户引导

用 observe 把短阶段信息送入现有事件流：

```chaos
async fn show(event) {
  await progress_emit("progress", event);
  null
}
// operators 是前文的五个函数；task 是本次输入。
let report = await E.run(snapshot, task, operators, observe=show);
```

observe 接收 `{phase,revision,ids,executions,total}`，total 为 4k；数据在阶段边界发布。`progress_emit` 生成真实 `bot.progress` 事件，网页执行事件、CLI `--stream`、SDK 和 SSE 都能读取，详见[实时交互](REALTIME.md)。它展示阶段和工作证据；模型公开输出仍沿原模型事件通道返回。`phase:"completed"` 仅表示 `E.run` 已构造好报告，发生在 Process 检查点提交之前；是否持久化成功还需核对该轮真实终态与 Process 记录。

库本身不消费用户指导队列。需要任务中途调整时，调用方可在操作函数的明确边界 `await guidance_receive()`，更新自己的任务策略；持续目标也可用 Process 邮箱提交下一项任务。用户输入不会被自动当成可执行源码；在途工具也不会因为收到一句指导就自动回滚。严格的 Bot 过期决策处理继续使用[原生 Bot](NATIVE_BOTS.md)。

## 跨任务保留经验

长期运行将 `report.snapshot` 放入 Process 的 state，下一轮重新读取。持续示例把本轮完整 records、selected 和学习引用放入 output，并通过 `process_step` 一起提交新快照、输出和消息确认。学习失败时，旧检查点保留，沿[Process 恢复规则](PROCESSES.md)处理。导入库本身不会写文件或启动守护进程。

**持续示例有更小的持久化限制：state 和 output 各至多 40,000 字符，整个 step 至多 100,000 字符。** `E.run` 的报告允许 240,000 字符，并不保证能直接装入 Process output。完整记录过大时，本轮检查点提交失败、原状态保留，Process 进入正常协调流程；系统不会为了重试存储而自动再次付费推理。学习时先用 `k=1` 和简短输出；生产程序可自行实现经过授权的外部证据归档，再保存精简回执。已发生的归档与模型调用仍是独立外部效果。

示例入口的输入 `persistent:true` 会创建持续函数，需要项目授予 `process.manage`。从网页长期工作入口或 SDK 给该 Process 发送下一项任务，宿主重启后仍读取已提交的快照；崩溃中的 Task、调用栈和未完成探测不会自动续跑或重放，需要按 Process 的正常协调流程处理。具体过程与能力授权见[持续函数](PROCESSES.md)；已有 `evolving-dot` 示例继续用于源码修改。

| 你要改变什么 | 使用什么 |
| --- | --- |
| 本任务的提示、操作策略、记忆或角色数据 | `std/evolve` 的候选、实际执行和组合 |
| 下一项任务的改进策略与经验 | 将新 snapshot 提交为 Process state；共同 base 不变 |
| 项目源码及对应持久状态 | [process_evolve](PROCESS_EVOLUTION.md)：真实 Program、测试、授权与原子采用 |
| 模型目录及路由候选 | [模型配置](MODEL_CONFIGURATION.md)的发现、autoUpgrade 和失败切换 |
| Chaos 安装包版本 | [replace／更新流程](UPDATES.md)，保留用户源码与配置 |

行为经验不会修改模型权重，也不能通过修改 prompt、guardrails 或 JSON 字段扩大宿主授权。源码变更仍需 `program.adopt`，不能用一次模型选择代替候选测试及版本检查。

## 失败时怎么处理

| 现象 | 检查方式 |
| --- | --- |
| 模型结果不能解析或字段错误 | 对照五种操作的返回合同；模型适配器直接解析 JSON，不修补正文或从代码围栏提取内容 |
| Unknown execution evidence ID 或对比引用不足 | 让 learn 只引用收到的 records.id，至少两个不同记录；不要使用上一轮同名记录代替本次观察 |
| 步数、时间或模型预算耗尽 | 先用 `k=1` 和紧凑任务验证；按宿主预算配置调整，不在捕获错误后伪造成功报告 |
| 持续函数进入 needs_reconciliation | 查看已完成效果与最后检查点，按 Process 文档显式协调；不要直接重放可能操作过设备或文件的探测 |

## 边界与可复现性

数据仅允许有限的 Null、Bool、Int／Number、String、List、Record，深度最多 32。函数、Agent、Program、Task、资源句柄及其嵌套值不进入快照；要保存 Agent 或 Program，显式保存其公开定义并在调用方重建。每个回调输入、输出和返回快照均通过 `json_copy` 做严格数据复制，遍历计入当前计算预算；它不会像显示序列化那样把句柄转换为标签。通用复制接口见[标准库 JSON](STDLIB.md#json)。

| 内容 | 上限 |
| --- | --- |
| base、strategy、task、每个 Harness、每次 probe 观察 | 各 8,000 字符 |
| snapshot | 30,000 字符 |
| 完整 report | 240,000 字符 |
| 保留经验／单次新增经验 | 16／8 条，旧经验超限从最早项移出 |
| cue／lesson／reason | 200／800／1,000 字符 |
| 每条新增经验的证据引用 | 2–16 个不同执行 ID |

这是数据上限，不保证最大 k 和最大数据在默认时间／步数内都能完成。所有 AST 执行、数据复制、模型与工具使用原运行预算。取消或失败会按原 TaskGroup 协议结束已接纳工作；库不发布成功的新快照，已产生的外部效果仍需依据真实证据协调。持久示例有意保存完整执行记录以便核对学习引用；它必须另外满足前述 Process 的检查点大小限制，不能仅凭 `E.run` 成功或公开 completed 事件认定已经提交。

实现参考了上游任务级 Propose–Probe–Compose、只从 composed 输出选择及任务后学习。这里使用语言内数据和真实回调，不兼容上游 OpenCode／Codex 文件布局、八类可编辑文件协议或完整 benchmark runner；没有引入它们的全局网络／沙箱设置。上游跨任务库可保留完整已采用 Harness，本库保留有界策略与经验，由用户在这些数据中选择可复用内容。论文的实验收益不能当作本系统的性能承诺。研究固定于上游提交 `1cfe254e8d25b15f37048c3730134686abe60335`，其源代码使用 Apache-2.0；此处是按方法编写的语言实现。

真实模型模式一次 `k=1` 就会调用 4 次工作 Agent、3 次改进 Agent 和1次学习 Agent；服务默认运行时限可能不足。按实际提供方延迟在自己的运行配置中设置合适时限，超时不会自动放宽预算或重试整个任务。试验过程中若热改模型配置、触发模型回退或外部环境变化，对比可能混杂；本例继续遵守系统原有路由，不能把这类对比当作固定模型实验的证明。
