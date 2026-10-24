# 大模型智能体：五个可运行示例

从一个问答函数开始，再组合多个角色、并发任务、语言函数工具和有状态 Bot。这些源码使用 `provider: "model"`，运行时会请求项目中配置的模型。它们仍是普通 Chaos 程序：数据传递、调用顺序、计算和终止条件由语言表达，模型负责理解与生成。

## 准备一个学习目录

先按[快速开始](GETTING_STARTED.md)准备含有 `chaos.py` 的项目，并按[模型配置](MODEL_CONFIGURATION.md)保存 Base URL、Key 和角色模型。本文只使用文本模型；不需要图像 Key，也不依赖 Codex App。完整环境第一次使用时运行 `./chaos setup`。每个项目自己的 `.chaos/settings.json` 是这些命令使用的配置。

在项目根目录新建 `learning` 文件夹，把下表中的源码下载进去，保留文件名。项目分发包内也有这些源码。先运行 `./chaos status`，在返回的 runtime 目录下找到 `chaos/examples/learning-models/`，复制到自己的 `learning/` 后再编辑和运行；全新未换版项目的 runtime 才是 `.chaos/runtime`。运行环境中的示例是只读学习资源，自己的副本不会被换版覆盖。

| 顺序 | 源码 | 学习重点 |
| --- | --- | --- |
| 1 | [main.chaos](../examples/learning-models/main.chaos) | 像调用函数一样调用一个模型智能体 |
| 2 | [pipeline.chaos](../examples/learning-models/pipeline.chaos) | 规划、写作、复核之间的显式数据依赖 |
| 3 | [parallel.chaos](../examples/learning-models/parallel.chaos) | `async fn`、冷 Task 与 `gather` |
| 4 | [function-tools.chaos](../examples/learning-models/function-tools.chaos) | 模型调用自己的语言函数，保留可检查的计算结果 |
| 5 | [goal-bot.chaos](../examples/learning-models/goal-bot.chaos) | 目标、上下文、证据和有界工作轮次 |

下列命令都在项目根目录执行。Windows 把 `python3` 换成 `py -3`；命令中的 JSON 字符串需要保留内层双引号。

```sh
./chaos check learning/main.chaos
./chaos fmt learning/main.chaos
./chaos run learning/main.chaos --input-json '"闭包是什么？"'
```

`check` 检查源码；`fmt` 只输出排版结果，不改写文件。它们不调用模型。`run` 才开始执行，真实回复、耗时与请求费用由提供方决定。`maxSteps` 约束一次智能体调用的步骤，不是整套程序的 HTTP 请求总上限；重试或候选模型切换仍可能产生额外请求。模型出错时先检查连接和所选角色模型，不需要修改语言语法。

## 1. 用智能体解释编程概念

[main.chaos](../examples/learning-models/main.chaos) 的完整源码：

```chaos
agent teacher {
  provider: "model",
  role: "planner",
  route: "learning-teacher",
  prompt: "用一个短例子解释输入中的编程概念。明确区分事实与建议。",
  tools: [],
  maxSteps: 3
}
teacher(input)
```

`input` 来自 `--input-json`；最后一行的值就是程序结果。试着把问题换成“纯函数为什么便于测试？”。返回值是模型生成的文本，不应与某一份固定答案作逐字比较。

`role` 选择项目的角色配置，`route` 是可单独覆盖连接的稳定名称。没有该路由覆盖时仍继承角色与提供方配置。更换模型只需修改配置，不必把 Key 或模型名写进函数。`tools: []` 不授予宿主工具。

## 2. 规划 → 写作 → 复核

[pipeline.chaos](../examples/learning-models/pipeline.chaos) 把一个任务依次交给 planner、executor 和 reviewer 角色。后一步的输入显式带上前一步结果；最后返回 `plan`、`draft`、`review`，便于对照每个角色的贡献。

```sh
./chaos check learning/pipeline.chaos
./chaos run learning/pipeline.chaos --input-json '"为第一次接触函数式编程的人写一份十五分钟入门教程"'
```

三个智能体声明后的组合函数如下；完整文件已包含声明，可以直接运行：

```chaos
fn develop(topic: String) {
  let plan = planner(topic);
  let draft = writer({topic: topic, plan: plan});
  let review = reviewer({topic: topic, plan: plan, draft: draft});
  {plan: plan, draft: draft, review: review}
}
develop(input)
```

这一组学习的是函数组合：规划文本是后续计算的数据。复核意见也只是模型输出，不能当作已经通过编译器、测试或事实核查的证明。要自动采用修改，应继续增加明确的验证与采纳条件。

可以先让三个角色使用同一可用模型，再在配置里分别选择规划、执行与复核模型。这能区分程序结构与模型路由，不要求准备三套 Key。

## 3. 同一问题，并发获得三个视角

[parallel.chaos](../examples/learning-models/parallel.chaos) 使用 `async fn` 包装智能体调用，再通过 `await gather(...)` 汇合。创建异步调用得到冷 Task，汇合才开始执行；结果的顺序与传入任务的顺序相同，而非网络响应的先后顺序。

```sh
./chaos check learning/parallel.chaos
./chaos run learning/parallel.chaos --input-json '"设计一个帮助小团队整理知识、回答问题的助手"'
```

输出是三个 `{perspective, answer}` 记录组成的列表，视角依次为“用户体验”“工程实现”“维护成本”。同一个 `critic` 智能体被当作 `Function` 参数传入，并为每次调用传入不同的 Record；无需复制三套模型实现。

观察三个视角如何分别指出收益、风险与建议。并发适合互不依赖的分析，不能替代上一节已有先后依赖的流水线，也不保证总耗时一定缩短。一个任务发生普通错误时，汇合请求取消剩余任务并等待已启动工作退出；已经发送的请求不会因此变成未发生。

进一步组合：将汇合结果作为输入交给一个总结智能体，或者用普通函数对结果排序、过滤、记录。完整任务合同见[并发任务](TASKS.md)。

## 4. 让模型使用语言函数工具

[function-tools.chaos](../examples/learning-models/function-tools.chaos) 把预算计算写成普通函数，用 `agent_tool` 描述输入、输出合同，再通过 `agent_bind` 交给模型。绑定后的调用产生冷 Task，使用 `await` 执行。

```sh
./chaos check learning/function-tools.chaos
./chaos run learning/function-tools.chaos --input-json '{"items":[{"unitCents":2500,"quantity":2},{"unitCents":1200,"quantity":3}],"limitCents":10000}'
```

金额使用整数分。语言函数先检查单价、数量与预算，再计算 `2500 × 2 + 1200 × 3 = 8600` 分；预算为 `10000` 分，因此余额是 `1400` 分。`calculate_budget` 工具接收 `null`，从闭包返回这份已计算的结果，模型不能通过工具参数替换原订单。

成功输出包含 `budget`、`explanation` 和 `toolCalls`。其中 `budget` 应为：

```json
{"totalCents":8600,"limitCents":10000,"remainingCents":1400,"withinBudget":true}
```

`explanation` 是模型生成的说明，`toolCalls` 至少为 1。如果模型直接回答却未调用工具，源码会抛出“未通过程序验收”的错误；不会用一段看似合理的文字冒充工具证据。单价、数量或预算不符合合同则在模型调用前失败。

模型负责理解输入、按协议调用工具和解释结果；乘法、求和及预算判断由实际语言函数完成。工具描述告诉模型如何使用函数，运行时的类型合同负责检查输入与输出。没有把回复中的某个词当作工具调用，也没有把提示词当成授权。

练习时改变订单或预算，观察 `budget` 与模型说明是否一致。工具执行证据只证明函数已返回这些数值，不能代替对模型文字的核对。完整 API 见[函数工具](FUNCTION_TOOLS.md)。

## 5. 用模型回调驱动有界 Bot

[goal-bot.chaos](../examples/learning-models/goal-bot.chaos) 将模型调用放进原生 Bot 的回调。`planner` 得到当前目标与上下文，`execute` 产生本轮结果，`verify` 明确返回 `done`、下一轮 `context` 和 `output`。这些回调由同一个语言运行时执行。

```sh
./chaos check learning/goal-bot.chaos
./chaos run learning/goal-bot.chaos --input-json '{"topic":"Chaos：智能体像函数一样调用，普通函数负责计算与流程组合。请概括这门语言。","minChars":10,"maxChars":30}'
```

目标是生成 10～30 个字符的中文说明，最多尝试三轮。`plan` 用普通函数整理主题、上一稿、长度反馈与用户指导；`execute` 调用 writer 模型；`verify` 用语言的 `length` 检查字符数。这里检验的是字符范围，不是字节数、token 数或内容质量。

输出包括 `status`、`done`、`output`、`iterations` 与 `context`。`output` 含正文 `text`、实际字符数 `characters` 和 `lengthAccepted`。第一稿已合格就结束；不合格时把上一稿及反馈放入下一轮上下文，不需要从回复中的“完成”一词猜测状态。

这里的轮次数量限制用来控制一次学习运行。达到迭代上限不等于任务已经完成；检查返回的 `done` 和 `status`。`achieved` 表示程序定义的完成条件成立，不是对模型内容正确性的额外认证；`needs_reconciliation` 表示需要核对已经发生的工作，不能直接重跑当作成功。

普通 Bot 的活状态属于当前运行。要跨服务重启保存长期目标，应再组合 [Process](PROCESSES.md) 和 [Session](SESSIONS.md)，而不是把 `max_iterations` 改成无限。Bot 的详细状态、指导与协调合同见[原生 Bot](NATIVE_BOTS.md)。

## 查看真实过程和追加指导

给任何一个运行命令添加 `--stream`，终端会逐行输出已持久记录的运行事件，最后输出同一 run 的 `run.result`。这展示调用、工作阶段和结果，不保证模型文本逐 token 输出，也不展示隐藏推理。

例如，查看 Bot 的事件流：

```sh
./chaos run learning/goal-bot.chaos --input-json '{"topic":"Chaos：智能体像函数一样调用，普通函数负责计算与流程组合。请概括这门语言。","minChars":10,"maxChars":30}' --stream
```

在 macOS／Linux 的交互式终端中，把末尾的 `--stream` 换为 `--interactive`。该模式包含事件流，并允许输入自然语言指导；Bot 在自身的指导边界接收新信息。收到 `queued` 只表示已入队，不表示已经采用。若已有外部动作发生，追加指导可能使本轮进入待协调状态，不会自动撤销或重放先前动作。

`/cancel`、Ctrl-C 或 Ctrl-D 会取消并等待这个命令拥有的本地运行退出。重定向输出或 Windows 环境使用 `--stream`，需要图形交互时使用工作台。普通单次问答或流水线不会仅因为加上交互参数就自动建立接收指导的循环。

## 验证自己的修改

先运行 `check` 排除语法和模块问题，再运行一个最小真实模型输入。对控制流、工具计算和返回结构的回归验证，使用[显式智能体 fixtures](AGENT_TESTING.md)：只替换模型响应，保留真实语言执行。

Bot 还需要真实运行宿主提供状态与指导协议，不能直接用无宿主纯求值器替代；它的离线回归应保留 Framework 和 Bot 控制器，仅替换外部模型响应。

本教程的离线回归不能证明提供方在线、账号有相应模型权限或模型一定选择工具。真实调用后的输出也不保证下一次完全相同。将确定性部分交给函数与测试，将生成性部分保留为可检查的建议、计划或内容，才能逐步扩展成可依赖的程序。
