# HTTP API 参考

接口契约随当前运行版本从 OpenAPI 定义生成。既有 `/api/v1` 调用方式保持兼容；浏览器、CLI 文件工作区及 Python Client 使用同一个服务。

后台任务与 IDE 助手也提供独立的 SSE 观察接口：`/api/v1/tasks/events/stream` 和 `/api/v1/projects/{projectId}/assistant/{jobId}/events/stream`。保持原 JSON 事件接口可用，支持 `Last-Event-ID`／`after` 续传；各事件库游标不能混用。完整契约与命令见[实时交互](REALTIME.md)。

## 读取精确契约

启动服务后读取 `GET /api/v1/openapi.json`，可获得请求体、响应与错误定义。独立 `chaos serve` 默认地址是 `http://127.0.0.1:8765`；目录 IDE 选择空闲端口，应使用启动终端打印的实际地址。客户端入口、项目生命周期与工具扩展见 [SDK 指南](SDK.md)。控制平面和模型配置见 [智能体框架](HARNESS.md)。

```sh
curl http://127.0.0.1:8765/api/v1/health
curl http://127.0.0.1:8765/api/v1/openapi.json
curl http://127.0.0.1:8765/api/v1/docs
```

## 调用和兼容约定

提交 JSON 请求时设置 `Content-Type: application/json`。创建、保存、运行和结构修改等端点具体字段以当前契约为准；有版本字段的操作必须提交刚读取的版本，过期会返回 409，不能盲目重发写请求。

v1 错误响应为 `{"error":{"code":"...","message":"...","details":null}}`。常见 HTTP 状态：400 参数错误、403 来源拒绝、404 对象不存在、409 版本或容量冲突、503 暂不可用。更新切换期间，页面自动重连；业务客户端应按业务语义重试读取，并避免重复提交尚未确认结果的写请求。

`GET /api/v1/projects/{projectId}/snapshot` 提供一致项目与事件游标。事件重连、精确数字以及 `inputJson` 的约定见 [实时一致性](REALTIME.md)。`/api` 保留旧页面接口，新集成使用 `/api/v1`。特别注意：旧 `/api/events` 返回 SSE，正式 `/api/v1/events` 返回分页 JSON；SSE 应显式使用 `/api/v1/events/stream`，不能仅通过去掉版本前缀猜测响应格式。

## 语言声明大纲

`POST /api/v1/language/analyze` 的请求方式保持不变，响应新增可选的 `outline` 定义，当前服务器会返回该字段。原 `symbols` 和定义跳转结构保持兼容。大纲携带当前文件、完整／截断状态及真实声明的层级，节点包括完整范围、名称范围和子项，偏移均为 UTF-16 单元。`kind` 描述语法声明类别，`detail` 沿用保守静态信息；不因名字或文本内容猜测功能。具体字段见当前 OpenAPI 与 [SDK](SDK.md)。网页导航必须把结果绑定到发起分析的草稿快照，不能把旧范围用于新源码。

## 编程助手的源码上下文

`POST /api/v1/projects/{projectId}/assistant` 保留 `version`、`prompt`、可选 `sessionId`，增加可选 `editorContext`。它包含完整当前 `files`、`entry`、`languageVersions`、`activeFile` 及 `selection: {anchor, head}`，选区按 UTF-16 单元校验。使用当前草稿原基准版本，不能以新读取的远端版本为旧草稿换基准。显式上下文不保存项目、执行程序或授予权限。

返回任务的 `editorContext` 给出 `draftHash` 等来源元数据；内容身份覆盖文件、入口与方言，不含光标或当前文件。候选 `changes` 相对提交的输入草稿计算。带上下文任务的 `/assistant/{jobId}/apply` 除 `version` 外必须提交当前 `editorContext`，前端检查本地草稿变化，服务核对请求快照与冻结输入相同及原项目版本后在原事务中应用并保存；不同草稿或版本返回冲突，不能自动重试。无上下文的既有启动、读取和应用调用保持原行为，完整记录结构见当前 OpenAPI。

## 情况与恢复调试

启动 source 调试时，debug 对象接受可选 `pauseOnCondition: false`。未处理 error_condition 可在明确开启后进入情况暂停；显式语言 invoke_debugger 使用同一机制。原接口和默认调试行为保持。

`GET /api/v1/runs/{runId}/debugger` 在情况暂停提供 `conditionRecovery`：纯 condition／restarts 列表及 total／truncated，不返回活回调或句柄。`POST` 同一路径用 `{command: "restart", pauseId, restartId, args: [], kwargs: {}}` 选择本次暂停实际发布的出口。pauseId 和 restartId 均需匹配；重名不会按名字替换。非法参数拒绝时原暂停保持，过期暂停返回冲突。

API 只提交复制后的有界严格 JSON，真正恢复在原解释器线程执行。取消或超时优先，普通 continue／step 拒绝情况恢复后传播原错误；源版本快照与已有项目版本 CAS 保持不变。详见[调试器](DEBUGGER.md)。

## 文档接口

`GET /api/v1/docs` 返回本版本文档目录、标题、分组、搜索文本与章节。`GET /api/v1/docs/{pageId}` 返回同一源文件的 Markdown、可安全展示的 HTML、章节锚点和版本。文档为随发行包提供的只读资料，不从任意磁盘路径读取文章。

服务面向本机可信的单用户工作区；Host/Origin 检查不是用户登录鉴权，不能把它当作现成的公网多租户 API。安装及部署边界见 [安装部署](INSTALLATION.md)。

## 0.18 目标、计划与技能

[目标、计划与可执行技能](AGENT_MODES.md)说明 Plan／Goal／Skill 的语言接口、持久化与恢复、真实工具录制、浏览器会话和 GUI／SDK 共同执行语义，以及当前实现边界。

## 0.19 模式库编写

Plan、Goal、Skill 现在支持统一验证、结构表单编辑、CAS 修订保存、固定版本加载和普通语言模块导出。界面保留与外部更新冲突的草稿；录制回执与新编写定义保持独立。详见[模式库编写与版本一致性](MODE_AUTHORING.md)。

## 智能体定义接口（0.20）

`GET /api/v1/agents/schema` 返回公开 config JSON Schema；`POST /api/v1/agents/validate` 接受 `{definition,name?}`，拒绝额外请求字段，返回 `{definition,definitionHash,source}`。此接口是无运行效果的验证／导出，不建立 Agent 仓库。语义与源码示例见[智能体定义值](AGENT_VALUES.md)。

## Team API（0.21）

`GET /api/v1/teams/schema` 返回 Team 定义的 JSON Schema 文档；`POST /api/v1/teams/validate` 接受 `{definition,name?}`，返回 `{definition,definitionHash,source}`，拒绝额外请求字段。它们与 Agent 配置 schema 接口并存，前者描述整个 Team 定义。见[团队指南](TEAMS.md)。

## 原生自编程（0.22）

新增 `/programs/schema`、`/programs/validate`、项目内 `/program-snapshot` 与 `/program-adopt`；均有 `/api/v1` 前缀。采用请求含 `version/definition/expectedHash/cases` 与可选 reason，拒绝额外字段；不接受外部 passed 报告。旧 `/program` 继续服务语法树编辑。见 [完整合同](SELF_PROGRAMMING.md)。

## 持久 Process 接口

`GET /processes/schema`、`POST /processes/validate` 验证公开定义。项目的 `/processes` 支持 GET 分页和 POST 创建；单项 `{key}` 支持 GET，子路径 messages/control/configure/reconcile 为 POST。创建携带项目 version；控制、配置和协调携带进程 revision。消息是持久入队（202），创建是资源建立（201），均不保证轮次已执行完。未知字段拒绝；状态冲突409。所有路径前缀 `/api/v1`；详见[持久函数](PROCESSES.md)。

## 原生会话 API

以下路径使用 `/api/v1` 前缀；所有写入拒绝额外字段，revision／项目 version 冲突返回409。

| 方法与路径 | 输入／结果 |
|---|---|
| GET `/agent-sessions/schema` | 共享创建 schema |
| GET `/projects/{id}/agent-sessions` | after/limit 分页 |
| POST `/projects/{id}/agent-sessions` | version,key,agent,contextLimit? →201元数据 |
| GET `/projects/{id}/agent-sessions/{key}` | 当前元数据 |
| GET `…/{key}/history` | after整数/limit分页，含消息轮次/状态/真实run |
| POST `…/{key}/turns` | version,revision,input,messageId? →202标准运行记录 |
| POST `…/{key}/fork` | revision,newKey,agent? →201新会话 |
| POST `…/{key}/compact` | revision,keepTurns →当前元数据 |
| POST `…/{key}/close` | revision →当前元数据 |
| POST `…/{key}/reconcile` | revision,evidence →放弃不确定轮次后的元数据 |

没有向历史直接注入 assistant/tool 消息的公开端点。完整协议及权限见[会话](SESSIONS.md)，机器合同以 `/api/v1/openapi.json` 为准。


## 电脑观察

环境接口共用语言 schema。结构校验不会产生行动；项目历史与图像独立分页/读取，不能传宿主路径。见[Computer API](COMPUTERS.md)。


GET `/api/v1/projects/{projectId}/computer-sessions?after=0&limit=50` 返回 `{items,after,hasMore}`；GET `/api/v1/projects/{projectId}/computer-sessions/{key}` 返回单个会话元数据。它们均只读，不包含 cookies/localStorage、内部租约或宿主路径。连接、归还、关闭和协调继续通过普通语言 Run，不能向 HTTP 注入活句柄或观察。CLI 对应 `chaos computer sessions PROJECT` 与 `chaos computer session PROJECT KEY`。

## 智能体测试用例

Program 采用请求的 `cases` 支持可选 `fixtures` 字段，与语言 `program_probe/program_adopt` 及 IDE `draft.evaluate` 共用验证器。OpenAPI 的 ProgramCase 描述完整字段；既有 `{input,expect,entry?}` 请求保持兼容。服务重新执行测试后才执行版本 CAS，不接受调用者提供的通过报告。完整 JSON 示例见[智能体协议测试](AGENT_TESTING.md)。

## Process 演进回执

现有 ProcessRecord 增加可选 lastEvolution，沿用原读取/list/SSE/CLI 和 SDK 接口。它是持久提交回执，不是新的授权输入；旧记录可以没有该字段。立即 program-adopt 请求和普通 Process step 协议不变，详见[ProcessEvolution](PROCESS_EVOLUTION.md)。

0.46 的 AgentSession 记录可含 lastToolBinding；pending 与历史消息可含 toolBinding，记录语言工具合同、bindingHash、runId 和 sourceVersion。它们是可选的执行证据，不是 JSON 可调用函数。已有 turns 接口继续普通会话调用，不会自动安装历史工具。见[持久函数工具](SESSION_TOOLS.md)。

## 编译报告

POST `/api/v1/language/compile` 接受 `source` 和可选 `files`、`entry`、`languageVersions`，返回 `chaos.compilation/1`。source 是入口镜像，未知字段被拒绝；只编译分析、不保存或执行项目。报告内容与语言 `program_compile` 同源，缓存状态可因是否带共享预算而不同。见[编译规范](COMPILER.md)。


## 活动运行的指导

`POST /api/v1/runs/{runId}/guidance` 接收严格对象 `{input, messageId?}`，其中 input 是有界 JSON，messageId 是可选的 1–128 字符标识。成功返回 HTTP 202 与 `{runId,messageId,revision,status}`；queued 表示入队，received 表示原程序已经接收。相同 ID 和数据返回原回执，不再次入队；不同数据、已结束或正在取消的运行返回冲突。未知字段拒绝。

指导作为数据进入原运行；没有新增授权、源码保存、任务重启或隐式模型消息角色。运行在显式 guidance_receive 边界消费。`guidance.queued`／`guidance.received` 与 `bot.progress {kind,data}` 使用既有运行事件及 SSE，后者只包含程序明确发布的公开进度、计划或结果。工作区、CLI、Framework 和 Client 使用同一接口和回执语义；它不属于后台 /tasks 指导接口。


原生 Bot 使用既有项目源码／图启动、运行读取与事件接口，没有新增 HTTP Bot 创建或隐式文本执行路由。指导 receipt 的 queued／received 不变；原生控制器另外发布 `bot.context.applied`（scopeId、messageId、revision）和动作准入／完成事件（scopeId、decisionId、revision、actionId、kind，结果含 status／errorCode）。事件 ID 是 Store 的真实单调 ID；JSON payload 不自报 eventId 来替代它。准入以当前原生决策和已接受指导 revision 的宿主原子检查为依据，不能从 progress 或结果数据伪造。共享合同见[原生 Bot](NATIVE_BOTS.md)。


## 0.109 持久 Process 指导

`POST /api/v1/projects/{projectId}/processes/{key}/guidance` 接受严格 `{input,messageId?}`，返回202与 `{key,runId,messageId,revision,status}`。状态 queued／received／applied 表示持久接受／运行接收／运行采用，不证明动作完成。输入最多40000规范 JSON字符，按原 Process 规则脱敏；原始哈希保留以支持清除正文后的幂等。不同用途／数据、旧控制 revision 或不确定动作按原冲突／协调合同处理。原 messages、control、configure、reconcile 和普通 Run 指导接口保持原入口。

## 跨轮 Process 实时记录

原 `GET /api/v1/events` 和 `GET /api/v1/events/stream` 支持 `processKey`，与 `runId` 互斥。验证项目、Process 和游标发生在 SSE 响应头之前；不存在或非法选择器仍返回普通 v1 错误响应。`after`／`Last-Event-ID` 是原始整数事件 ID；重连保持它，不能改为轮数或指导 revision。

Process 事件的 `processOrigin` 由宿主生成，区分可信生命周期和真实运行的公开事件。普通回调的同名事件不具有生命周期权限。Process 页附加 `historyStartEventId`／`legacyHistoryIncluded=false`；默认项目和 run 页面形状保留。SSE 在单个 run 完成后继续观察长期 Process；25 秒连接期限后仍可从已交付游标重连。分离传输不会停止 Process。

## 宿主服务生命周期

`ServiceSpec`、`ServiceManager`、`ServiceError` 是本地 Python SDK，CLI `chaos service` 使用同一规范。`ServiceManager.plan(spec)`、`install(spec)`、`status(name)`、`start(name, timeout=30)`、`stop(name, timeout=20)` 与 `uninstall(name)` 管理当前用户拥有的操作系统服务；`ServiceSpec.to_dict/from_dict` 交换规范。它们不是运行中的 HTTP 服务上的任意进程管理接口，没有新增远程 install/start/stop/uninstall 路由。

HTTP `GET /api/v1/health` 仍用于真实服务健康与实例身份核验；已有版本更新、项目运行、任务、Process 和配置 API 不变。OS 任务的已安装/加载/进程/就绪状态必须分别检查，配置生成或注入适配器结果 `actualVerified: false` 不能当作真实运行证明。源工作区、托管更新 profile、路径和清理规则见[安装部署](INSTALLATION.md#无界面常驻部署)。

## 可验证的源码型编译产物

`compile_artifact(source, files=None, entry="main.chaos", *, language_versions=None)` 返回 `chaos.compiled-artifact/1` JSON 数据；`load_artifact(data)` 通过同一实现返回新的原 `ProgramBundle`。产物保存闭合源码、方言、编译器／标准模块／依赖身份及可重定位的便携流模板，不保存 Env、Cell、Function、Task、能力、运行计数、PC 或原生地址。它包含源码，应按源码的保密要求保存。

装载会重新解析、链接、展开宏并确定性降低，逐项验证保存的模板、效果与节点路径；自行重算摘要不能使被修改的 CFG 合法。加载不是前端绕过，也不是 AOT 或执行授权。模块初始化、默认值及工具等运行期效果只在原执行路径实际到达时发生，重复装载和不同求值器保持独立环境。

模板仅在源码明确调用原 `compile_function(..., target="auto-flow" 或 "native-flow")` 且函数匹配时由原编译入口采用；函数形成不会自动编译。解释目标、未匹配的动态函数与不受支持部分保留原行为；实际 LLVM 准入仍取决于宿主可选依赖。旧 `chaos.compilation/1` 报告及 `compile_program`／`Framework.compile`／原 `chaos compile` 不变，报告不是新的运行输入。

SDK 导出同名两个函数；`Framework.compile_artifact`／`Framework.load_artifact` 使用同一实现，`Framework.validate_artifact` 只返回原包摘要。显式执行可以使用 `Framework.evaluate(load_artifact(data), bindings={"input": 40})`，仍是原纯求值合同，不新增工具、模型或设备权限。

命令行 `chaos artifact build FILE --root DIR` 向标准输出写 JSON；`chaos artifact validate artifact.json` 重新验证并输出包摘要；`chaos artifact run artifact.json --input-json 40` 才显式验证并纯求值。JSON 文件参数也接受 `-` 读取标准输入；命令不改写输入文件，不隐式加载宿主凭据或运行用户项目。

只读 HTTP 提供 `POST /api/v1/language/compile-artifact`（原 source／files／entry／languageVersions 请求）和 `POST /api/v1/language/validate-artifact`（仅 artifact 字段）；`Client.compile_artifact`／`Client.validate_artifact` 对应这两个入口。没有 HTTP 执行产物接口。HTTP 沿用原请求 4 MB／300000 数据值与响应 8 MB 限额；本地 SDK／CLI 的核心产物上限为 canonical JSON 8 MB／400000 值／256 层、512 模板及32768总指令，单模板仍受原1024指令等配额约束。CLI 输入文件另限原始 UTF-8 JSON 8 MB，带额外空白的文件也计入该输入限制。较大的本地合法产物不保证能通过更小的 HTTP 请求边界。

本节描述当前接口，实际版本测试、分发与安装分别验收，不声称加载提速、跨目标原生 ABI、可保存 continuation、独立 GC、完整 Python／Lisp 或物理机器人能力已经完成。AgentTool／Team／Bot 与 Robot 软件动作继续使用原运行时、权限、取消和不重放合同。
