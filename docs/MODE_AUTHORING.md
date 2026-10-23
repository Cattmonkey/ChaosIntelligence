# 模式库编写与版本一致性

0.19 将 Plan、Goal、Skill 的定义纳入同一条编写链路：结构表单、语言值、SDK 与 HTTP 共用验证器及模式仓库。模式库对象和程序模块是两种明确的持久化对象；库保存不会偷偷改写程序中的字面量。模式不是提示词模板识别器，调用由真实语法、类型和值决定。

## 定义是一等语言值

`mode_from(definition)` 显式验证 Record 并构造受控值，拒绝未声明字段、非法依赖和非法参数路径。普通 JSON 不会自动获得执行能力。`mode_info(value)` 返回可修改的定义副本，修改副本不会改变原值。

```chaos example=authoring-plan-value
let definition = {kind: "plan", name: "answer", steps: [
  {id: "compute", call: "worker"}
]};
let pipeline = mode_from(definition);
fn calculate(ctx) { ctx.input + 1 }
await pipeline(41, {worker: calculate})
```

输出 `{compute: 42}`。调用目标仍是函数、智能体或受支持的可调用值；定义只记录绑定名称，不保存闭包或权限。Plan 默认按原有确定性顺序串行执行；显式设置 `max_concurrency: 2` 等 1–128 的整数时采用有界依赖调度，不从分叉推断并行。

```chaos example=authoring-goal-value
let target = mode_from({kind: "goal", objective: "得到 42",
  planner: "planner", verify: "verify", max_iterations: 2});
fn planner(ctx) { plan([{id: "answer", call: "calculate"}]) }
fn calculate(ctx) { ctx.input + 1 }
fn verify(ctx) { {done: ctx.results.answer == 42,
  state: ctx.results.answer, evidence: {actual: ctx.results.answer}} }
await target(41, {planner: planner, calculate: calculate, verify: verify})
```

手工技能使用 `{kind: "skill", actions: [{tool, input}], parameters: [{name, step, path}]}`。参数路径明确指定 Record 键或 List 下标；不会全局替换文本。创建定义不会执行工具；回放时仍检查当前项目权限和实际工具。

## 可执行源码与来源

`mode_source(value, name="saved_mode")` 输出一个普通模块，形如 `let saved_mode = mode_from(...); export {saved_mode};`。0.39 起导出自动调用语言格式化器分行缩进，不改变规范定义哈希；它能通过现有 Parser、AST、模块导入和 IDE 分析。已有 `skill_source(skill, name="recorded")` 继续导出普通可调用函数，并使用相同格式化器改善布局。

```chaos example=authoring-metadata
let original = plan([{id: "x", call: "worker"}]);
let imported = mode_from(mode_info(original));
let a = mode_metadata(original);
let b = mode_metadata(imported);
[a.kind, a.origin, a.definitionHash == b.definitionHash]
```

结果为 `["plan", null, true]`。哈希基于验证后规范定义：对象键顺序和省略的空依赖不影响哈希，步骤及动作顺序保留。Plan 省略 `max_concurrency` 或显式设置 1 规范成同一旧定义；大于 1 的上限参与哈希。`mode_metadata` 返回副本。源码和手工导入不携带可信宿主来源，不能通过在 Record 中伪造 `origin` 获取历史执行证据。

## 按修订加载和保存

语言中的以下操作返回冷 Task，只有等待或调度后才访问仓库：

```chaos
let current = await load_mode("pipeline");
let fixed = await load_mode("pipeline", revision=3);
let info = mode_metadata(fixed);
let changed = mode_from({kind: "plan", name: "revised",
  steps: [{id: "answer", call: "worker"}]});
await save_mode(changed, "pipeline", revision=info.origin.revision);
```

不传 revision 的 load 在效果执行时读取最新修订；传正整数则精确读取该修订。已加载值不会随库更新变化。`save_mode(value,key)` 仍然仅创建新键；修改必须传当前修订，过期写入冲突，不覆盖更新。布尔值不是修订号。

加载成功会在同一数据库事务中记录 `mode.loaded`，包含项目、键、实际修订、定义哈希和运行源码哈希，关联 runId。来源元数据为 `{projectId,key,revision,definitionHash}`。修订号也可能因 Goal 检查点推进而增加，定义哈希用于区分定义变化。

源码快照哈希只覆盖源码包。它不会自动固定所有动态加载的库依赖；需要可复现的程序应显式固定每个依赖修订。持久 Goal 的既有检查点、初始输入、源码身份和租约约束继续生效；这不等于恢复任意调用栈。

## 可视化与无界面共同编写

打开“目标、计划与技能”面板，可建立计划步骤、依赖和并发上限、目标规划与验收绑定、工具动作及参数路径。表单先调用同一语言验证器，再以当前修订保存。异步验证、历史读取和 SSE 不替换未保存草稿；SDK 在后台更新后，界面保留本地内容并提示版本冲突。可以明确重载、继续保留或另存新键。

导出默认仍将 Skill 转成普通函数，Plan/Goal 导出值模块；显式 `format=value` 可统一导出三类值。导出绑定所选修订。导入 IDE 只新建本地模块草稿，保留其他源码草稿，须通过项目保存提交。尚未保存的结构或图形编辑需先完成，避免跨编辑模式丢失内容。

录制 Skill 的 checkpoint 是真实动作回执，定义不能原地改写；已成功录制的技能可以明确复制为新的人工定义，新对象没有录制回执。未完成录制禁止加载及导出，也不通过界面复制为可执行技能。用户显式编写的动作定义属于新程序，不代表录制成功。已有检查点的 Goal 不允许原地改变定义，可另存为新目标。

## SDK 与 HTTP

```python
from chaos import Client
api = Client("http://127.0.0.1:8891")
definition = {"kind": "plan", "name": "answer",
              "steps": [{"id": "compute", "call": "worker"}]}
checked = api.validate_mode(definition)
created = api.save_mode("workspace", "answer", checked["definition"])
source = api.mode_source("workspace", "answer",
                         revision=created["revision"], format="value")
assert source["definitionHash"] == checked["definitionHash"]
```

`Framework` 提供相同方法。`POST /api/v1/modes/validate` 接收 `{definition,name?}`，返回规范定义、定义哈希和源码，不产生运行效果。`GET /api/v1/projects/{id}/modes/{key}/source` 支持 `revision`、`format=value|function`、`name`。写入、历史及详情继续使用既有模式接口。纯语言计算和离线定义验证不依赖 Codex App。

更多语义见[目标、计划与可执行技能](AGENT_MODES.md)、[运行一致性](REALTIME.md)和[语言路线图](LANGUAGE_ROADMAP.md)。本版本未宣称 Python/Lisp 标准库、运行时或生态的全面兼容。

## 语言内的受验证自我迭代

角色智能体可以产生下一版 Plan 的结构化定义，程序通过 `mode_from` 验证结构、实际运行候选计划、检查结果，然后通过 `save_mode(..., revision=...)` 提交修订。这些步骤是普通语言计算与显式效果，可以组合成函数，也可用于无界面宿主。界面通过同一模式事件显示提交后的版本。

例如下列流程中的 `architect` 是返回 Plan 定义的智能体，`bindings` 是本程序明确提供的函数和智能体，`verify_candidate` 是返回严格 Bool 的验收函数：

```chaos
let previous = await load_mode("pipeline");
let candidate = mode_from(architect({current: mode_info(previous)}));
let evidence = await candidate(input, bindings);
if verify_candidate(evidence) == true {
  await save_mode(candidate, "pipeline",
    revision=mode_metadata(previous).origin.revision)
} else {
  throw "候选计划未通过验收"
}
```

提交后的新修订不会修改 `previous` 或正在执行的旧值。其他程序须在下一次加载时采用新版本，或继续固定旧修订。并发提交使用 CAS；不把自然语言“改好了”当作提交成功。运行候选计划可能产生外部效果，验收失败只阻止版本发布，不撤销已发生的工具动作。

端到端回归使用实际 Agent 调用及本地确定性 Provider：智能体增加审核步骤，语言执行和验收后保存，旧值仍执行原计划、新修订执行新增步骤；验收失败时库修订保持不变。这里验证的是模式定义的演化。0.22 起，闭合 Chaos 模块包的反射、候选纯测试与项目版本采用已由 Program 的独立效果实现，见[语言内自编程](SELF_PROGRAMMING.md)。这些机制不提供恶意候选的操作系统安全隔离，也不修改宿主解释器或自动发布外部软件；不保证任意修订正确或优于旧版本。
