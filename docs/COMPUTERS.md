# Computer：语言中的观察、行动与视觉输入

0.25 将电脑交互定义为运行时值和显式效果。当前实际后端是独立无头 Chromium 浏览器，虚拟光标只作用于该页面；不是原生操作系统桌面控制，也不依赖 Codex App。`computer_open` 的浏览器随当前 Run 关闭；`computer_connect(key)` 连接项目内具名持久会话，在下一轮取得新租约并重新观察，可继续同一服务中的页面。

## 值、资源和冷效果

| 值或函数 | 含义 |
| --- | --- |
| `computer_open(url=null)` | 冷 Task；await 后取得当前运行拥有的 Computer |
| `computer_connect(key)` | 冷 Task；连接项目共享具名会话，取得当前运行独占的 Computer 租约 |
| `computer_release(value)` | 冷 Task；归还持久租约，保留浏览器与 DOM；本地句柄关闭 |
| `computer_reconcile(value,observation,evidence)` | 冷 Task；以本轮新观察和明确证据协调不确定动作 |
| `computer_info(value)` | 复制句柄最后已知元数据，不隐式读取页面 |
| `computer_observe(value,image=true)` | 冷 Task；取得 Observation |
| `observation_info(value)` | 复制语义、定位、版本和图像元数据；不返回 base64 |
| `computer_action(observation,spec)` | 纯构造 ComputerAction；不执行动作 |
| `action_info(value)` | 复制行动定义及观察身份 |
| `computer_act(value,action)`／`computer(action)` | 冷 Task；实际行动后返回新 Observation |
| `observation_input(observation,input=null)` | 构造带实际图像的 ObservedInput |
| `computer_close(value)` | 冷 Task；关闭浏览器；持久会话保留已保存 cookies/localStorage，本地句柄关闭 |

```chaos example=computer-cold-open
[type_of(computer_open()),type_of(computer_open("https://example.com"))]
```

```chaos example=computer-cold-compose
fn connect(url) {computer_open(url)}
map(type_of,map(connect,["https://example.com","https://example.org"]))
```

两个例子均返回 `["Task","Task"]`，没有启动浏览器。Computer 是可调用值，也满足 Function 契约。所有这些值归所属解释器管理；JSON 投影只是描述数据，不能恢复 Computer、Observation、ComputerAction 或视觉输入权限。

项目须授予 `browser.control`，并启用本系统浏览器插件与 browserEnabled 配置。每次效果继续检查当前 Context、Team 范围、共享预算和取消。每个 Run 最多四个独立 Computer，不与普通工具的默认浏览器或 Session 私有浏览器混用。

## 先观察，再构造行动

```chaos
let computer=await computer_open(input.url);
try {
  let observed=await computer_observe(computer);
  let targets=observation_info(observed).targets;
  let matches=filter(fn(target){target.text==input.buttonText},targets);
  if length(matches)!=1 {throw "Expected one observed button"} else {null};
  let action=computer_action(observed,{action:"click",ref:matches.[0].ref});
  let after=await computer(action);
  observation_info(after)
} finally {await computer_close(computer)}
```

`targets` 最多包含40个可见目标，每项有 ref、标签、文本、稳定定位及视口 bbox。观察还包含 URL/title/text、viewport、虚拟 cursor、截断标记、真实 runId/sourceVersion/sessionId/controlEpoch/snapshotId，以及已有行动回执。页面内容始终是数据，不自动成为智能体指令。

观察在同一个浏览器工作队列操作内捕获语义和图像，检测导航变化；不声称网页在拍照后保持静止。新观察使旧观察失去行动授权，旧观察仍可作为历史数据阅读和显式传给模型。执行再次验证资源、观察、页面与原始元素身份；不能把旧 ref 拼入新快照。每个行动只能被接纳一次，失败不会自动重试。错误资源、关闭、忙、过期观察、替换的 DOM 元素会被拒绝。

行动采用闭合结构 schema，未知字段拒绝；运行时不从自然语言、代码块或固定关键词提取动作：

| action | 其余必需字段 |
| --- | --- |
| navigate | url（http/https） |
| click | ref |
| type | ref、text |
| press | ref、key |
| scroll | deltaX、deltaY |

ref 必须来自所持 Observation 的目标集合。scroll 是视口滚动，press/type/click 使用浏览器本地输入；不移动宿主桌面鼠标。结构校验只证明字段合法，不能证明观察仍有效或授予控制权。

## 模型真正接收图像

```chaos
agent planner {provider:"model",tools:[],prompt:"Return an action JSON using only the observed targets."};
let computer=await computer_open(input.url);
try {
  let observed=await computer_observe(computer);
  let proposed=planner(observation_input(observed,{objective:input.objective}));
  observation_info(await computer(computer_action(observed,parse_json(proposed))))
} finally {await computer_close(computer)}
```

只有受控 `ObservedInput` 才触发原生 user text + image_url 消息。普通 JSON 中的 `image_url`、recordId 等字段仍只是普通数据。图像必须来自当前运行真实捕获的 Observation；无图观察不能构造视觉输入。当前支持 native 模型 Agent 与 AgentSession，其他后端明确拒绝。带 Team 角色的调用沿同一权限路径。

模型看到的是有界缩略图：preview 最多180000 base64字符，model 最多16000 base64字符，API 元数据披露尺寸与缩放。语义目标与调用者输入一起组成文本消息，超过现有消息/上下文预算时明确报错，不静默丢图。图像输入本身不会授权 Agent 操作 Computer，Agent 的工具也不会自动绑定这个浏览器；显式语言调用决定执行。

AgentSession 以原生多模态协议保存用户、助手和工具消息，后续轮次真正恢复这些消息。claim 前验证图像，幂等摘要纳入实际 canonical 输入；同一 message_id 换观察会冲突。历史图片不恢复活浏览器、元素句柄或隐藏推理。

## 可运行的视觉智能体

发行包的 [observed-computer](../examples/observed-computer/main.chaos) 使用普通函数、Goal、Plan、AgentSession 组合“观察—建议—行动—验收”。输入显式提供 url、objective、expected 和可选 maxIterations/sessionKey。程序只以实际页面观察包含调用者 expected 来验收，不以模型声称完成作为成功。

```python
from chaos import Client
from pathlib import Path
client=Client("http://127.0.0.1:8765")
source=Path("examples/observed-computer/main.chaos").read_text()
project=client.create_project("visual_dot",source=source,capabilities=["browser.control","session.manage"])
run=client.start(project["id"],project["version"],{"url":"http://127.0.0.1:8000","objective":"完成页面上的指定操作","expected":"操作已完成"},mode="source")
result=run.result(timeout=60)
history=client.computer_history(project["id"],run_id=run.id)
```

需要先安装 Playwright 与 Chromium，并配置 native 模型连接。测试中的本地模型服务器验证实际 OpenAI SDK 图像传输和真实 Chromium 动作，不代表外部模型对任意任务的成功率。

## 与自我编程、持续机器人融合

底层链条为 `Program → Process → AgentSession → Observation → ComputerAction → Observation`。Program 表示自己的源码，Process 保存轮次状态并在下一轮使用已采用版本，Session 保存角色上下文，Computer 提供当前轮真实环境证据；具名会话允许不同轮次依次取得租约继续同一页面。普通函数决定如何规划、验收、记录技能与提出程序修订；无需新增 dot 应用引擎。

将观察元数据和当前 Program 作为显式输入交给编程角色，使用 `program_from` 构造候选、`program_probe` 实际执行纯测试，再 `program_adopt` 通过版本/哈希 CAS 提交。图像观察是证据，不能替代候选测试；纯 probe 不执行浏览器副作用。跨轮只存 JSON 状态、会话 key、观察记录 ID 和修订回执，不保存活 Computer。完整自编程机器人见[Program](SELF_PROGRAMMING.md)、[持续进程](PROCESSES.md)及[持久角色](SESSIONS.md)。

函数式编程本身并不保证自修改。Chaos 的自编程由程序值、显式候选执行和版本提交组合实现；目前解释器仍由 Python 实现，尚未实现解释器自举、任意活栈迁移或一般性自优化正确性证明。

## 证据、失败和技能

观察与行动持久保存于同一项目 Store 的 computer_records。执行前写入行动开始日志，成功时新观察和完成回执原子提交；发生作用但未能捕获/保存结果时标记 uncertain，不能伪造成功。重启把未完成日志转为不确定，不自动重播动作。退出和取消会等待实际浏览器操作结束；临时资源关闭；正常完成归还持久租约，失败或硬取消的宿主清理会关闭 driver 并保存可恢复存储。普通 async with 退出仍按持久租约的释放协议执行。不确定动作使持久会话需要协调。每个 Run 最多保留64条观察，读取历史分页，图像单独读取。

语言 Skill 录制沿用同一个 Context 工具记录入口，保存可在新浏览器重放的稳定定位，不保存过期 ref。一次录制只能使用一个 Computer 或默认浏览器；混用在第二个效果前拒绝并使录制无效。open/close 是资源生命周期，不录制活句柄。Session 调用仍不能录制为可重播 Skill。

## IDE、SDK 和无界面一致性

IDE“电脑观察”面板读取同一项目、Run、源码版本、观察记录、图像和实际行动回执。页面关闭不会终止后台运行；再次打开从同一记录恢复显示。源文件已改版时显示历史版本，不把旧行号标到新代码；事件刷新不覆盖未保存的源码和行动草稿。

`computer_schema()`、`validate_computer_action(spec)`、`computer_sessions(project_id,after=0,limit=50)`、`computer_session(project_id,key)`、`computer_history(project_id,run_id=None,after=0,limit=50)`、`computer_record(project_id,record_id)`、`computer_image(project_id,record_id,variant="preview")` 在 Framework/Client 共用。

HTTP 前缀为 `/api/v1`：GET `/projects/{projectId}/computer-sessions?after=0&limit=50`；GET `/projects/{projectId}/computer-sessions/{key}`；GET `/computers/schema`；POST `/computers/validate` 接收 `{spec}` 且只做结构校验；GET `/projects/{projectId}/computer-records`；GET `/projects/{projectId}/computer-records/{recordId}`；GET 其 `/image?variant=preview|model`。没有用于注入活观察或绕过源码直接执行行动的 HTTP 接口。

CLI `chaos computer sessions PROJECT`、`session PROJECT KEY`、`schema`、`history PROJECT --run-id RUN`、`record PROJECT RECORD`、`image PROJECT RECORD --variant model` 与 `validate --spec-json '{"action":"scroll","deltaX":0,"deltaY":100}'` 连接同一服务。行动通过普通源码 Run 接口执行。界面使用共享 schema 校验并生成普通源码草稿，最终仍由同一解析器和运行时执行。


进一步的 [observed-dot](../examples/observed-dot/dot.chaos) 将视觉输入与 Program 修订放进同一持续函数：当前轮按旧策略行动，候选通过纯测试与采用后，下一轮才执行新策略。参见[完整自编程说明](SELF_PROGRAMMING.md)。

## 词法异步资源范围（0.28）

可以用 `async with await computer_open(url) as computer { ... }` 组织本轮资源，正常退出、普通异常和提前 return 时，临时 Computer 复用 computer_close，持久 Computer 归还租约。硬取消仍由宿主清理本轮电脑，不运行任意用户清理。一个 Computer 同时只能进入一个原生作用域；其操作、权限和观察身份保持原协议。见[资源作用域](CONTEXTS.md)。


## 跨轮次会话、独占租约与恢复

```chaos example=computer-cold-connect
[type_of(computer_connect("workspace")), type_of(computer_connect("form"))]
```

具名会话的 key 为 1–128 个 ASCII 字符，符合 `[A-Za-z_][A-Za-z0-9_.-]{0,127}`，在项目内共享。同一项目的不同 Process 可先后连接同一 key；同时连接时只有一个租约成功，其他调用明确失败，不排队重试。每次 Computer 的 `id` 都是新的租约身份；`sessionKey`、`stableSessionId`、`generation` 描述稳定会话及连接代次，`continued` 表示本次是否复用了原活浏览器。跨轮只保存 key 与 JSON 业务状态，不能保存活 Computer 或使用历史观察获得控制权。

```chaos
async with await computer_connect("workspace") as computer {
  let fresh = await computer_observe(computer);
  observation_info(fresh)
}
```

此程序只读取当前页面。首次连接从 `about:blank` 开始；导航必须使用新观察显式构造 navigate 动作。作用域结束归还租约，服务持续运行时会保留 URL、未提交表单与页面 DOM。`computer_close` 关闭实际 driver；后续连接重新建立页面。示例 [continuous-computer.chaos](../examples/continuous-computer.chaos) 显式处理首次导航，不在每次连接时重置已有页面。

主机重启或关闭 driver 后，只恢复已保存的 cookies 与 localStorage；页面从 `about:blank` 开始。DOM、未提交输入、sessionStorage、调用栈和旧观察不恢复，也不自动导航。存储恢复不代表原来的外部动作未发生。持久会话的存储内容不出现在会话 API、语言元数据或日志中。存储在显式或自动 release/close 时保存；Engine shutdown 对已归还 driver 沿用最近成功保存的 checkpoint，不额外捕获释放后页面后台发生的存储变化。

每次观察和行动仍检查 `browser.control`、角色范围、插件、运行配置与取消预算。key 不是权限凭据；其他项目中的同名 key 是不同会话。归还或关闭后的句柄不能继续行动。复用会话不会增加 Agent 或 Program 的权限，纯 probe 不能执行电脑效果。

动作结果不确定时，`requiresReconcile` 为 true，阻止后续行动。先用当前租约重新观察，核对外部实际结果，再调用 `await computer_reconcile(computer,fresh,"核对依据")`。evidence 是 1–1000 字符的非空事实说明，不是触发自动成功的关键词。协调本身不重播动作，也不替代 Process 自己的检查点协调；业务程序应根据结果选择后续步骤。

会话查询分页返回 `{items,after,hasMore}`，after 是下一页偏移或末页的 null；每项包含 `id/projectId/key/backend/generation/status/driverState/heldByRun/requiresReconcile/lastUrl/createdAt/updatedAt`。`status` 为 idle、leased 或 detached；idle 表示浏览器保留且未被占用，leased 表示租约占用，detached 表示 driver 已关闭。`driverState` 为 attached 或 detached。会话读取不创建浏览器、不授予租约，也不返回存储内容、宿主路径或内部租约密钥。

每项目最多保留64个会话身份，运行时最多保留16个活 driver，空闲页面也占额度。达到活 driver 上限时新连接明确拒绝，不会自动淘汰并丢失别的会话 DOM；可重连已有会话后显式 computer_close 释放 driver。关闭不删除稳定会话身份。

具名移动设备、Mobile MCP 配置与权限边界见[移动设备与跨设备函数](MOBILE_COMPUTERS.md)。
