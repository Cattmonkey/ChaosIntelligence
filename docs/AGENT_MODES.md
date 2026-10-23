# 目标、计划与可执行技能

0.18 将目标、计划和录制技能纳入语言值与执行效果。它们使用现有 Parser、AST、Evaluator、Task 调度器、Context 权限及项目数据库。自然语言描述目标；显式构造器、函数绑定和返回值决定执行，不依赖固定提示词或关键词识别。

## 语言设计

智能体是有外部效果的一等可调用值。Plan 用名字绑定真实的函数、智能体或可调用对象；Goal 调用规划函数产生 Plan，再调用验收函数判断完成；Skill 保存实际工具动作并重新执行。这三种值不包含持久化的闭包、浏览器对象或执行权限；动作输入仍需按下文规则脱敏和参数化。`mode_info` 显式导出定义；普通 Record 即使含 `kind` 字段也不会自动成为可执行模式。

| 构造／操作 | 结果 | 语义 |
| --- | --- | --- |
| `plan(steps, name="plan", max_concurrency=1)` | Plan | 1–128 个步骤，验证唯一 id、依赖存在、无环；并发上限 1–128 |
| `run_plan(value, bindings, input=null)` | 冷 Task | 按拓扑顺序调用步骤，得到步骤 id 到结果的 Record |
| `goal(objective, planner, verify, max_iterations=8)` | Goal | 显式规划、验收绑定名称；最多 1–100 次迭代 |
| `pursue(value, bindings, input=null, key=null)` | 冷 Task | 规划、执行、验收，返回完成状态与证据 |
| `record_skill(action, input=null, parameters=[])` | 冷 Task | 成功后返回 `{output, skill}` |
| `replay_skill(skill, values={})` | 冷 Task | 重新调用工具，返回真实结果列表 |
| `save_mode(value, key, revision=null)`／`load_mode(key, revision=null)` | 冷 Task | 创建或 CAS 保存模式／显式加载经过验证的定义 |
| `mode_info(value)` | Record | 返回定义的副本 |
| `skill_source(skill, name="recorded")` | String | 生成可编辑的普通 Chaos 函数模块 |

这些函数可以传递、别名、组合和用于宏生成的普通调用语法。`p(input, bindings)` 等价于 `run_plan(p, bindings, input)`，`g(input, bindings)` 等价于不持久化的 `pursue(g, bindings, input)`，`skill(values)` 等价于 `replay_skill`。Task 在 `await`／调度前不执行动作。

## 计划与角色绑定

```chaos example=modes-plan
let pipeline = plan([
  {id: "prepare", call: "builder"},
  {id: "review", call: "reviewer", needs: ["prepare"]}
], "prepare and review");
fn build(ctx) { ctx.input + 1 }
fn review(ctx) { ctx.results.prepare * 2 }
await pipeline(20, {builder: build, reviewer: review})
```

结果为 `{prepare: 21, review: 42}`。每步收到 `{input, results}`；`results` 只含声明的直接前驱。所有用到的绑定会在第一个步骤执行前校验。返回的 Task 由该显式组合器等待；未消费的流、非 JSON 输出会失败。默认保留原来的确定性串行扫描顺序。0.39 增加显式 `max_concurrency`：大于 1 时按依赖就绪状态调度；图形分叉本身不会改变串行默认值。

生产程序可将 `builder`、`reviewer` 绑定到两个不同角色的 Agent。它们沿用已有角色模型路由、工具范围、步骤预算及取消机制。修改计划不扩大项目授权。

## 有界并发计划（0.39）

```chaos example=modes-parallel-plan
// 有界依赖调度：独立研究并行，审核只接收声明的直接前驱。
async fn research(ctx) {
  await sleep(0);
  ctx.input + 1
}
async fn implement(ctx) {
  await sleep(0);
  ctx.input + 3
}
fn review(ctx) {
  ctx.results.research + ctx.results.implement
}
let pipeline = plan([
  {id: "research", call: "research"},
  {id: "implement", call: "implement"},
  {id: "answer", call: "review", needs: ["research", "implement"]}
], name="parallel roles", max_concurrency=2);
await pipeline(19, {research: research, implement: implement, review: review})
```

返回 `{research: 20, implement: 22, answer: 42}`。上限约束同一次 Plan 调用中同时进行的直接步骤，包含等待该步骤返回的 Task 的时间。它不限制步骤内部显式创建的整个后代任务树；真实外部效果仍受宿主工作线程与原有预算限制。纯计算在同一合作式解释器中推进，不承诺多核并行。

所有动作绑定和初始 JSON 输入先校验，再启动第一个步骤。每次按声明顺序挑选就绪步骤，完成的结果立即释放后继，不等待无关的慢分支；步骤只收到它声明的直接前驱结果。不同外部效果的完成顺序可能不同，程序应通过 `needs` 明确表达顺序。嵌套 Plan 的并发上限分别生效，预算仍共享。

运行时复用原生 TaskGroup，创建、纳管并收束本次的步骤任务。任一步骤失败后停止新调度，取消并等待已启动的自有任务；错误传播到外层 `catch` 前收束在途自有工具效果，保留硬取消和资源超限的优先级。动作新创建并返回的、尚未启动的私有冷 Task 由本次步骤收束；返回已有 Task 的别名只借用等待，不接管或取消原拥有者的任务。引用混合私有和外部任务的聚合不能伪装成全新的私有任务。已经发生的外部效果不能回滚。

Plan 可以直接调用函数、Agent、Team 角色或 Program；Goal 的 planner 返回并发 Plan 后仍沿用严格验收与检查点，Process 也须等 Plan 收束才能提交本轮状态。项目权限、角色能力范围及版本身份不会因并发扩大。单次 `record_skill` 仍拒绝重叠工具效果，失败录制不能发布为完整线性技能。

每次调用的事件包含 `executionId`、`name`、`definitionHash`、`maxConcurrency`；并发步骤附 `stepTaskId`。界面按运行与调用身份区分同时执行的相同计划，显示实际步骤状态。它们是执行记录，不是模型隐藏思维。

`mode_info`、持久仓库、SDK、HTTP 和源码导出都使用同一个可选定义字段 `max_concurrency`。省略或显式设置 1 会规范成原来的无字段定义，保持旧哈希；其他值参与定义哈希和修订校验。完整示例见 [parallel-plans.chaos](../examples/parallel-plans.chaos)。

## 目标与严格验收

```chaos example=modes-goal
fn planner(ctx) { plan([{id: "next", call: "advance"}]) }
fn advance(ctx) { ctx.input + 1 }
fn verify(ctx) {
  {done: ctx.results.next >= 42,
   state: ctx.results.next,
   evidence: {observed: ctx.results.next}}
}
let target = goal("达到 42", "planner", "verify", 4);
await pursue(target, {planner: planner, advance: advance, verify: verify}, 40)
```

返回 `{done: true, state: 42, evidence: {observed: 42}, iterations: 2, status: "achieved"}`。规划函数接收 `{objective, state, iteration, results, history}`，必须返回受控 Plan。模型若提供结构化建议，程序仍须通过 `plan(...)` 显式构造、校验。

每轮先消耗一次迭代，再规划、执行，最后验收。验收必须严格返回 `{done: Bool, state: JSON, evidence: JSON}`；文本 `DONE`、非空字符串、数字不代表成功。达到次数上限但未通过验收则为 `exhausted`。证据是应用验收数据；运行时不把模型自述自动当作客观事实。

传 `key="stable_goal"` 将检查点持久化到当前项目。重跑同一固定源码包、同一定义及初始输入会复用检查点；已完成目标不重复动作，源码或初始输入不同则拒绝复用。运行中 lease 与 revision 防止两个运行同时拥有目标。每轮 `pending` 先落库，再执行外部效果。

取消或崩溃发生在 pending 时，状态为 `needs_reconciliation`：外部动作可能已发生，系统不会猜测并盲目重放。调用 `reconcile_goal` 提交核对后的 state、evidence 和当前 revision，再重新运行原程序恢复。已经消耗的迭代次数不重置。这里恢复的是显式检查点，不是任意函数调用栈；每次运行仍有自身资源预算，跨运行保留的是目标迭代额度。普通已授权计划无需逐步额外批准。

## 录制真实动作与参数化回放

下面程序需在具有 `memory.put` 能力的项目运行。纯 `Framework.evaluate` 不具备录制数据库与工具宿主。

```chaos
fn write(value) { tool("memory.put", {key: "message", value: value}) }
let capture = await record_skill(write, "第一次", [
  {name: "message", step: 0, path: ["value"]}
]);
await save_mode(capture.skill, "write_message");
await replay_skill(capture.skill, {message: "第二次"});
skill_source(capture.skill, "write_message")
```

录制入口在工具实际执行前后收集回执，包括 Agent 内部调用的工具。函数计算与模型回答不作为可重放动作。参数绑定是动作序号和结构路径，支持 Record 键、List 下标及空路径替换完整输入；不存在路径、重叠路径或缺少参数会失败，不进行文本全局替换。

录制 scope 传递给后代 Task，不捕获无关任务；封存前等待已启动后代，未启动冷任务取消且不执行。录制范围内创建的生成器与迭代器在封存前关闭，清理阶段的工具动作也纳入录制；范围外的活流不能混入录制，关闭失败不会发布完整技能。单次录制不接受重叠工具执行，避免把无法确定顺序的并发操作封成线性技能。异常、取消、超限或无完整回执的录制保留为 `incomplete`，不能回放。历史动作没有事务回滚。

回放重新检查当前工具权限、执行真正工具并收集新结果，不读取旧输出冒充执行。配置密钥经已有脱敏规则处理；不可重放的脱敏输入需要显式参数覆盖。其他业务敏感字段仍应由应用明确参数化，不能把自动录制理解为能识别所有秘密。`save_mode` 省略 revision 时仅创建新键；传当前 revision 可以 CAS 更新同一键，宿主 API 使用相同规则。

生成源码为普通模块，依然可以格式化、导入、修改、调试和图形编辑。与直接 `replay_skill` 一样，工具权限仍来自运行项目。模块函数中的工具调用同步返回；若希望以冷任务组合，可在自己的 `async fn` 中调用它。

首版录制范围为**语言运行中执行的工具动作**。尚未实现对用户在任意桌面程序中的鼠标键盘演示进行录制，也未实现自动归纳参数或自动安装技能。

## 浏览器控制的会话与引用

`browser.control` 现在在同一运行内共享一个有独立工作线程的 headless 浏览器会话。`navigate`、`snapshot`、`type`、`click` 等操作沿用原工具入口；不移动全局系统光标。

新 snapshot 返回 `sessionId`、`controlEpoch`、`snapshotId` 以及 targets。使用 ref 操作时须带完整身份字段；导航、动作或新 snapshot 后旧 ref 失效，跨会话引用也会拒绝。录制时保存 `replayInput` 的稳定 locator，回放在当前页面重新定位，要求目标唯一。旧 selector 调用兼容保留，但没有新 ref 的快照校验保证。

这是浏览器会话隔离，不是所有 macOS／Windows 程序的虚拟桌面。Agent-S 的全局 pyautogui 执行方案未作为语言中的不受控 eval 引入。取消会等待在途浏览器操作结束并清理会话；不能撤销已经发生的页面操作。

## IDE、无界面、HTTP 与持久化

工作区的“目标、计划与录制技能”区域从同一个项目模式仓库读取定义、依赖、迭代、证据与回执，按既有 SSE 事件刷新。源码操作、SDK、HTTP 共用同一版本数据。复制技能源码固定 revision，避免复制期间把不同版本混为一谈。界面显示执行摘要和证据，不声称暴露模型隐藏推理。

```python
row = app.save_mode(project_id, "pipeline", definition)  # 仅创建
row = app.save_mode(project_id, "pipeline", revised_definition,
                    revision=row["revision"])           # 比较并交换
page = app.modes(project_id, kind="plan")
old = app.mode(project_id, "pipeline", revision=1)
history = app.mode_history(project_id, "pipeline")
app.reconcile_goal(project_id, "stable_goal", revision, checked_state,
                   evidence={"verified": True})
```

`Framework` 与 HTTP `Client` 具有相同方法。REST 路径见 [HTTP API](HTTP_API.md)：`/projects/{id}/modes`、`/{key}`、`/{key}/history`、`/{key}/reconcile`、`/{key}/source?revision=N`。模式数据使用现有 SQLite Store，增量增加 `mode_artifacts` 与 `mode_revisions` 表。旧项目、接口和已有表保留；数据库重启恢复只标记中断，不自动重新执行未知效果。

调试器可预览受控值定义，不执行用户属性或回调；断点和单步仍在真实步骤函数中触发。IDE 提供构造器及调用结果类型提示。图形结构来自相同调用 AST，无第二套执行语义。

## 上游研究与取舍

本实现独立编写，研究参考了以下源码设计，未直接合入上游运行时：

| 项目／固定版本 | 借鉴 | 本语言中的实现 |
| --- | --- | --- |
| [OpenDots](https://github.com/CopilotKit/OpenDots/tree/625452e06cde74cb25b0ce319e2c1be0488f5a5f) | 角色任务、持久 lease、带身份的电脑操作 | 显式函数绑定、Goal lease/CAS、快照引用校验 |
| [Agent-S](https://github.com/simular-ai/Agent-S/tree/3aa272d23d2994c7bbde1acbbe0ef8e8d06b8693) | 规划与执行拆分、可编程电脑动作 | planner → Plan → 函数／Agent／tool → typed verifier |
| [Playwright codegen](https://playwright.dev/docs/codegen) | 结构化动作与可再定位 locator | 真实工具回执、稳定 locator、明确参数路径、源码导出 |
| [Skyvern](https://github.com/Skyvern-AI/skyvern) | 工作流事件、结构化参数与定位 | 同一模式仓库、revision 历史、执行摘要 |

这里超越单独聊天模式开关的部分是：这些能力成为可传递、可组合、可调试的语言值，并能脱离 GUI 执行。尚不能声称功能全面超过上述项目，也不因此声称已经拥有 Python／Lisp 的全部生态与特性。图灵完备性来自语言的递归、条件分支和可增长数据结构的抽象计算能力；有限资源的 Goal 组合器本身不提供无限执行承诺。见 [语义与边界](SEMANTICS.md) 与 [语言能力路线图](LANGUAGE_ROADMAP.md)。

## 0.19 编写与修订扩展

增加 `mode_from`、`mode_source`、`mode_metadata` 和可选 revision。模式库可视编辑和源码接口共用定义验证；已加载值不受后续写入影响。详见[模式库编写与版本一致性](MODE_AUTHORING.md)。


## 0.25：观察与模式组合

Plan/Goal 可通过普通函数组合 Computer 观察、AgentSession 图像输入和 ComputerAction。Skill 保存真实工具稳定定位；单次录制绑定一个电脑资源，跨资源或 Session 效果拒绝。详见[Computer](COMPUTERS.md)。
