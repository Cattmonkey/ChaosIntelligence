# 智能体控制平面与 Harness

Chaos 仍拥有函数语言、程序版本、项目工具授权及执行快照。控制平面拥有可持久化的部署配置和异步任务。Harness 负责模型与工具之间的循环。工具以声明式 schema 注册，模型调用原生 function/tool_calls；自然语言回答原样返回，不按关键词、Markdown 或固定 action 文本猜测行为。语言语法、协议字段和模型标识符仍按各自正式契约解析。

## 安装与启动

先按 [安装与部署](INSTALLATION.md) 创建 Python 环境并安装源码或 wheel，再启动：

```sh
python -m chaos serve --port 8765
```

若使用目录项目，先显式运行 `python3 chaos.py setup` 准备依赖，再用 `python3 chaos.py` 打开 IDE；模型配置页是终端所报地址下的 `/control`。目录 IDE 使用系统分配的空闲端口，不固定为 8765。上面的 `serve` 则默认在 `http://127.0.0.1:8765/control`；两种入口使用同一控制平面和 Harness。

普通 native 模型调用无需安装浏览器或 Codex；只有对应工具/后端才需要可选依赖。独立 Codex CLI 的实际绝对路径填入控制台 `codexPath`，不使用 Codex App 中的二进制、账号或 RPC。服务默认只绑定本机；控制台能运行本机代码，适用于受信任的单用户主机。

普通服务的配置默认位于 `~/.config/chaos-intelligence/settings.json`，可用服务参数 `--config` 或 `CHAOS_CONFIG` 选文件。目录项目显式使用自己的 `.chaos/settings.json`，CLI 和 IDE 读取同一项目设置，运行历史仍各自独立。嵌入 `Framework()` 默认使用内存设置；要读取文件须显式传入 `Framework(settings=Settings(path))`。

通过 `Settings.update` 保存的配置使用 0600、原子替换、版本 CAS 和跨进程写锁；公开配置投影不返回完整密钥。空白密码保留原值，逐智能体清除密钥覆盖后恢复继承。不要把凭据写入源码、前端包或任务输入；不要将含凭据的本地文件提交或打包。配置热更新应用于下一次请求/迭代，不在进行中的请求里替换凭据。连接步骤和可执行学习示例见[模型连接与配置](MODEL_CONFIGURATION.md)。

## 让程序改进自己的 Harness

[std/evolve](HARNESS_EVOLUTION.md) 在语言内组合提案、真实执行对比、组合和经验提炼。它调用已有 Function／Agent／Team 与模型配置，产生普通数据快照和公开阶段进度；Process 可跨任务保留经验。该能力与下面的模型目录升级、安装包更新分别使用各自的接口和验证边界。

## 配置、路由和模型发现

`providers` 配置 `baseURL`／`apiKey`／`model`；`roles` 配置各角色的提供方、模型和候选策略；`agents` 按路由 ID 覆盖角色、提供方、地址、密钥、模型或后端。解析先选 `agents[id].role` 或调用者角色，再合并角色与智能体覆盖；路由缺失的地址、密钥或模型才回落到提供方。默认角色已有模型，所以只改 `providers.text.model` 并不会替换角色已选模型。固定一个模型时，同时指定目标角色的 `model` 并关闭该角色 `autoUpgrade`。界面保留未保存草稿，并对另一客户端的更新提示版本冲突。

字段名是本系统的 `baseURL`／`apiKey`；传给 OpenAI Python SDK 时才转换为 `base_url`／`api_key`。本系统不会自动读取 `OPENAI_API_KEY` 或 `OPENAI_BASE_URL`。其兼容环境入口是 `CHAOS_MODEL_URL`、`CHAOS_MODEL_KEY`、`CHAOS_MODEL_NAME`，只有 URL 非空才一起启用；三项应完整设置，不能只设置密钥或只设置地址。URL 应填写服务的 API 根地址，例如 `https://YOUR-MODEL-HOST/v1`，不是一次请求的完整路径。

环境兼容统一由 Settings.resolve 在每次路由解析的临时副本中处理，不再为普通或绑定语言 Agent 重建设置。URL 非空时覆盖 text 提供方的连接与文本角色的模型，保留更具体的 agents 覆盖、宿主自定义客户端、插件和权限设置；候选目录不会从旧端点带到临时端点。普通 Agent、绑定 Agent、Session、UI 生成与 IDE／后台任务使用同一规则。临时值不会写入配置文件，移除变量后恢复持久配置；配置公开视图及模型目录发现仍针对保存的提供方配置，不把临时连接的发现结果保存进旧端点目录。学习演示推荐只选环境或文件一种来源，持久化教程先移除三项环境覆盖。精确脚本见[模型连接与配置](MODEL_CONFIGURATION.md)。

定时 /models 发现独立于 UI 请求运行，默认每小时刷新，也可手动检测。autoUpgrade 在已发现目录内按显式 families 和数字版本排序；planner 优先 Astra，再使用 Sol 候选；executor/reviewer 选择 Sol 家族新版本。排序只解析模型 ID，不分类任务文本。没有模型目录时使用显式配置。自定义 agent/role URL 或 key 不复用另一端点的目录。404/408/409/422/429/5xx 或连接失败会切换已配置候选，失败模型短时冷却；401/403 不掩盖凭据问题。模型目录并不保证每次推理授权，最终以调用结果为准；未实现无依据的主观质量自动评级。

native 对每一批工具调用先完整验证，再执行任何工具。空缺或非法 JSON 参数、未声明工具、重复调用 ID 等结构协议错误会将当前模型记为失败，切换已配置候选；不从回答文本猜测或补造参数。最多尝试 8 个去重候选，候选切换共享同一次运行的时间和行动预算，不修改持久化模型配置。所有候选均失败时返回明确的协议错误。只有通过响应验证才记录模型成功；工具自身的可恢复执行错误仍作为观察结果交给模型处理。

语言 Agent、绑定 Agent、Session 和绑定 Session 从已获准的宿主描述构造同一种 wrapped native 声明：参数必须恰为 `{input: value}`，并保留真实的八种 JSON 兼容输入类型。运行时预检沿原语言 `check_type`：Int 不接受 Bool 或整数值浮点数，Number 不接受 Bool；展示的 JSON Schema 不是另一种更宽的运行时类型系统。任一调用违反合同，整批均不会执行或记录 assistant 工具意图；Session 在请求前已记录的 user 消息不会撤销。此类协议错误沿原候选重试规则处理，只重试当前模型响应。

后台 Control 继续用已有的显式平铺 code／browser／image／delegate 参数 Schema；语言工具不依据内建名字替换已注册扩展的输入合同。纯声明投影只复制元数据，不执行工具、读取资源或授予权限，也未新增任意 Registry JSON Schema 接口。

切换候选保留当前对话及已完成工具的结果，只重试当前模型响应，不重新执行先前工具；非法批次不会写入对话或执行其中的有效调用。取消或时间预算耗尽会终止候选重试。模型切换事件只记录固定格式说明，不暴露原始参数或 HTTP 内容。

提供方地址、凭据及模型 ID 由部署者填写，不存在对所有账号都有效的示例模型目录。比如服务地址 `https://YOUR-MODEL-HOST/v1`、模型 `YOUR_MODEL_ID` 和密钥 `YOUR_API_KEY` 都只是占位符，必须换成自己的真实配置。先保存提供方，再设置角色的提供方和模型；想固定模型时关闭该角色 `autoUpgrade`。不要把密钥放入 Chaos 源码或版本库。

## 框架与插件关系

- native：使用 openai Python SDK 的 Chat Completions 原生工具调用，可调用本系统的代码、浏览器、图像工具及有限深度 agent.delegate；子任务显式指定角色，可分别路由到规划、执行、复核模型。
- codex：通过独立 `codex exec --json` 运行，单独临时 CODEX_HOME，自定义 Responses provider/base_url/env_key；不读取 App 登录。它使用 Codex 自身的代码工具。当前 exec 适配器没有宿主工具回调桥，Chaos 语言/图智能体使用 native 以保持项目能力边界；选择 codex 会明确拒绝并指向后台任务；后台 Codex 任务使用自身工具，不虚假宣告支持浏览器回调。
- plugins：实际复用 DeepSeek Harness/Cordis 的依赖服务、pending→loading→active、失败回收、逆序清理和运行身份逻辑。code/browser/image/native/codex 是可开关的服务；派发确实通过这些服务实例。新增插件由可信 Python 宿主注册，网页不能任意导入代码模块。
- UI：`web/dsh-state.js` 移植上游 process-activity、RunningStatus 和插件状态处理，用于工具计数、持续时间与工作态势。原文件、MIT许可、提交号及适配范围在 `vendor/deepseek/UPSTREAM.md`；它是明确的跨语言/无框架移植，不是完整 DSH JavaScript 插件 ABI。

## 后台任务、代码与浏览器

任务立即进入持久队列；后台线程处理模型请求和工具，界面不等待整项任务。并发可热调整。迭代记录持久保存，界面当前卡片逐轮覆盖、历史可展开；停止会传播取消，晚到模型回答不会继续执行工具。重启后正在执行的任务标记 interrupted，排队任务仍留在持久队列，可显式恢复；执行中的有副作用迭代可能需要核验产物，恢复不承诺外部效果恰好一次。

代码解释器支持 Python 和 shell，保留任务产物，限制时间/输出并清理子进程组。它是可信本机进程，工作目录不是安全沙箱；代码拥有服务账号的文件和网络权限。高风险或不可信任务应将服务运行在容器/独立账号中。

浏览器使用独立无头 Chromium/context，不移动系统指针，不夺取其他应用焦点。工具提供可访问性快照、可观察元素目标、虚拟光标位置和截图；图像附件以模型原生视觉输入发送。它不是对所有原生桌面应用都有效的“通用虚拟鼠标”。浏览器操作在有限超时内响应取消。截图和图像产物保存在任务目录。

半透明浮层展示状态、操作摘要、模型切换和迭代事件，不展示模型私有思维链。浮层外区域不拦截页面操作，支持折叠及减少动画设置。代码、工具、并发、超时和迭代有明确资源边界，“能做所有事”不意味着无限权限或无限运行。

## 机器人身份与外接设备

长期机器人可以用[原生 Bot](NATIVE_BOTS.md) 的显式规划／执行／验证回调，组合普通函数、一等 Agent、AgentTool、Team、Process 与 Program。Bot 返回原调度器的冷 Task，并沿原调用、效果和权限合同执行；它不会按目标文本自动选择模型或授予工具。持久会话身份、消息和版本另见 Session／Process／Program 的对应合同。群消息使用显式收件人参数，私聊仅经授权摘录进入群会话；DeepSeek-Bot 的群聊／记忆／插件设计对照及差异见[机器人与外接设备](ROBOT_DEVICES.md)。用户已确认所指为 `FeiZhuLulu/DeepSeek-Bot`（现重定向至 `Fei2411/DeepSeek-Bot`）；研究仍固定于文档注明的提交。树莓派 4B／5 的键鼠执行通过[Linux HID Device 后端](RASPBERRY_PI.md)接入。现有 Cordis 生命周期适配与社区 Bot 插件是不同来源，不承诺 DSH 插件 ABI 兼容。

外接 USB HID 是显式 `device.control` 效果，按租约、冷 Task 和异步资源退出执行；它作用于目标电脑的输入，不等同于浏览器的隔离虚拟光标。Bridge ACK 只证明报告处理，不能证明目标应用完成任务。设备配置、固件及实际验证边界见同一指南。

## 无界面调用

```sh
python3 -m chaos task start '检查任务目录中的数据并生成分析脚本' --role planner --iterations 3
python3 -m chaos task list
python3 -m chaos task events --after 0
python3 -m chaos task cancel TASK_ID
python3 -m chaos task resume TASK_ID
```

```python
from chaos import Client
client = Client()
config = client.settings()                    # 脱敏配置
client.configure({'version': config['version'], 'runtime': {'maxSteps': 20}})
job = client.start_task('生成并测试一个排序函数', role='executor')
print(client.task(job['id']))
```

语言中的 provider=model 走同一套配置；config.role/config.route 对应控制台角色和智能体 ID。语言代码工具需同时出现在项目 capabilities 和主体 tools 中。示例：`tool("code.execute", {language: "python", code: "print(6 * 7)", timeout: 1})`。既有 local provider 保留为明确标注的确定性演示，新增界面智能体默认 model。

## 上游资料

- [Codex as a platform](https://developers.openai.com/blog/codex-as-a-platform)
- [Codex open source](https://github.com/openai/codex)
- [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)


自定义工具、提供方及插件服务的完整 Python 示例见 [开发者指南](DEVELOPER.md)，项目语言智能体的能力契约见 [SDK](SDK.md)。部署更新独立于模型路由，参见 [更新安装](UPDATES.md)。

## 0.18 目标、计划与技能

[目标、计划与可执行技能](AGENT_MODES.md)说明 Plan／Goal／Skill 的语言接口、持久化与恢复、真实工具录制、浏览器会话和 GUI／SDK 共同执行语义，以及当前实现边界。


## 语言运行的指导与公开进度

当前项目的真实 Function／Task 可以通过 guidance_receive 和 progress_emit 实现协作控制器；[完整示例](../examples/guided-bot.chaos)使用原求值器、能力与预算。API／SDK／工作区发送到同一运行队列，bot.progress 的 progress／plan／result 是程序明确发布的公开数据，不是模型隐藏思维链。后台 /tasks 与 Codex 任务没有因此获得语言运行队列；它们保留原接口。接收是合作式的，不自动打断在途工具；收到指导也不表示它请求的改变已经完成。


## 原生 Bot 的长期目标与动态上下文

[原生 Bot](NATIVE_BOTS.md)是新的真实可调用值：bot 保存复制的 goal/context 和实际 planner／execute／verify 回调，调用返回冷 Task；std/bot 四个导出与全局原语身份相同。BotContext／BotResult 的字段提示来自同一 NativeSpec，不按名称或返回对象猜测语义。模型仍由显式回调通过已配置 Agent／AgentTool 调用，不提供默认认知或新增权限。

原生 Bot 的决策身份沿原 Task／Team／Program／绑定 AgentTool 传播，已接受指导与实际效果准入共用宿主锁。只有执行主体未进入且规划阶段没有外部动作前缀时，旧提案才自动重新规划。规划中的工具／Program 等已开始动作也属于前缀；任何 tool:* 即使声明 effect="pure" 仍保守处理。有前缀或已进入执行主体后过期，则保留完成／不确定工作并停止 needs_reconciliation，程序检查证据后显式 bot_reconcile，不自动重放。现有合作式 guided_bot 与 guidance_receive 合同保持可用；跨运行持久目标仍使用 Process／Session，永久运行和隐藏推理直播没有因此实现。


终态提交也在宿主同一锁下校验指导 revision，并封闭当次 Bot scope；直到 scope 关闭，新的指导明确拒绝。已经接受的相同 messageId 与相同内容仍返回原幂等回执，不再入队。非终态上下文提交不封闭 scope，之后接受的新指导仍可令旧决策过期。这个短暂封闭只保护当次终态提交，不把整个运行、持久目标或已发生的效果变成可撤销事务。
