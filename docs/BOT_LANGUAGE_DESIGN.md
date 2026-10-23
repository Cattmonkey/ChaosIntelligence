# Bot 能力的语言设计与开源对照

Chaos 的机器人是一组可调用值、数据状态与受控外部效果的组合。普通函数负责算法与控制流，Agent 负责模型调用，Session 保留长期对话，Process 保留跨轮调度与状态，Computer／Device 管理实际环境资源，Program 管理可检查的程序版本。GUI、CLI、SDK 和模型工具使用同一 AST、运行时和权限合同。

本页记录当前开源源码对照和语言设计，不代表已经移植上游产品的全部功能。研究没有执行上游程序，也没有复制其源码；机制到语言的映射是本系统的设计判断。

## 研究的项目身份

用户已确认本轮指定的 OpenBot 是 `isl-org/OpenBot`，研究时该地址重定向至 `ob-f/OpenBot`。它以 Android 手机的感知／计算连接实体车辆。regnull/openbot 和 CopilotKit/OpenBot 是分别列出的同名项目对照，不能替代指定项目的研究来源。以下链接固定研究提交，避免上游后续变化改变结论。

| 项目／研究提交 | 核心机制 | Chaos 的实现位置及边界 |
| --- | --- | --- |
| [regnull/openbot · 30d4a6c](https://github.com/regnull/openbot/tree/30d4a6c607fd0aa7f7e90354f516af29b3366747) · MIT | 持久 Actor inbox、Bot 串行处理、后台记忆反思 | Session 身份、Process 邮箱、普通函数／AgentTool 的明确交接；语义记忆整理与原生持久问答仍需专门设计 |
| [CopilotKit/OpenBot · 6d86ba4](https://github.com/CopilotKit/OpenBot/tree/6d86ba47369c46e1216cbf9f4ee4ce4840d4db90) · MIT | 每个 coworker 的环境与工具授权、结构化委派、动作前决定与动作后记录 | Team 权限交集、Computer 租约／观察／一次性动作、原 Context 事件；未提供其整个容器与 AG-UI 应用运行环境 |
| [defog-ai/opendot · 687c15b](https://github.com/defog-ai/opendot/tree/687c15b5ca18c0b90853a8b02e33158e3fae5e6b) · Apache-2.0 | 宿主动作注册、从任务重建动作目标、宿主凭据、预算和 SQLite inbox／outbox／schedule | 原 Context／Registry 的封闭准入、Process 持久身份与指导、公开动作结果；不移植上游 Docker、订阅 CLI 登录、Slack 或默认自动研究 |
| [CopilotKit/OpenDots · 625452e](https://github.com/CopilotKit/OpenDots/tree/625452e06cde74cb25b0ce319e2c1be0488f5a5f) · MIT | 后台任务领取、独立环境策略、接管和学习服务 | Process 领取／版本、Computer 观察来源、Program 测试／采用；外部云学习、语音／Slack 和自动多 Dot 委派不是已经实现的语言特性 |
| [Fei2411/DeepSeek-Bot · 5c27262](https://github.com/Fei2411/DeepSeek-Bot/tree/5c2726279d3bcabaad6aea403c46c88fcf9ba8d8) · Apache-2.0 | 多角色会话、群会话、上下文整理和定时提示 | Team 实际角色函数、Session、Channel／TaskGroup／std/swarm、Process；显式会话压缩不等于自动语义 steward |
| [simular-ai/Agent-S · 15776a8](https://github.com/simular-ai/Agent-S/tree/15776a8dc659658b1963816ea006f7af1978e595) · Apache-2.0 | 观察、规划、反思与独立视觉 grounding | Plan／Goal 的实际函数与 verifier，Computer 的观察和动作分离；原生桌面视觉模型及 pyautogui 脚本兼容尚无 |
| [isl-org/OpenBot（研究仓库 ob-f/OpenBot） · 216ecb0](https://github.com/ob-f/OpenBot/tree/216ecb018ebe26cfae68f15ed42397493d5861c6) · MIT | Android 手机的感知／计算与实体车辆控制 | 已核对固定固件／Vehicle 串口协议并接入原生 Robot profile；Android 摄像头／视觉／导航与物理车辆验收仍未实现 |

用户提供的 `FeiZhuLulu/DeepSeek-Bot` 重定向至 Fei2411；`isl-org/OpenBot` 重定向至 ob-f。Agent-S 和 DeepSeek-Bot 的当前提交相对先前研究版本只改变文档／发布元数据，其核心文件已逐一核对。许可证描述用于标明研究来源，不构成整仓移植。

## 把行为表达为函数与数据

持续身份由 Session／Process 的明确 key 表达，输入中的收件人是程序数据。语言调用目标是实际 Function／Agent／Team 角色值；普通聊天文本中的 `@name`、`PASS`、代码围栏或固定回复词不会自动改变控制流或权限。模型需要提出受控动作时，通过 AgentTool 的结构化参数调用真实语言函数。AgentTool 可显式声明语言专属权限，让真实回调组合 Process、Session、Program、Device 和 Robot；该委派与普通 Agent.tools 分开，按实际项目／Team／Bot 调用链求交，默认空权限且不能从名称或 JSON 恢复。模型可直接调用的宿主工具仍只来自原 Agent.tools。

下面的计划由本语言函数执行。换成 Agent 的调用仍沿原效果路径，控制流和计划数据不需要重写：

```chaos example=bot-callable-plan
fn worker(context) { context.input * 2 }
let work = plan([{id: "reply", call: "worker"}]);
let answer = await work(21, {worker: worker});
answer.reply
```

结果为 `42`。计划里的 `call` 是 bindings 中的明确键，传入的是实际可调用值；它不是从提示文本中猜出函数。多轮目标使用 Goal 的 planner／verify 绑定；验证结果决定完成状态。并发轮次与拓扑反馈使用 [std/swarm](SWARM.md)，长期执行使用 [Process](PROCESSES.md)。这些机制已经属于同一语言，而不是六套 Bot 框架并排运行。

Agent 的宿主工具合同和 AgentTool 的真实回调合同通过同一个纯 native 声明投影传给模型；这一投影连接原 Agent／Session 四条调用路径，Bot 内组合它们时沿用相同规则。它只把已有的八种 JSON 兼容语言输入类型描述出来，不读取描述文字猜测动作、不按内建名称给宿主扩展添加更窄的隐式合同。native 对整个响应批次执行实际语言类型预检，之后才发出工具开始事件、持久 Session 工具意图和执行回调。既有权限交集、冷任务、别名次序、Session 消息身份及重放语义保持原规则。后台 Control 的工具仍使用显式平铺参数合同，不能与语言 `{input: value}` 包装混用。

## 一个原生函数描述合同

当前全局环境中的 336 个 Native 都有明确的 NativeSpec：参数名、位置／关键字模式、默认值、返回类型、等待类型、记录字段和保守推导规则。注册时的 `Native.spec_id` 连接真实可调用身份与描述；源码中的别名保留身份，用户函数遮蔽原语后不会继承它的类型或效果提示。真实参数绑定、资源所有权和权限检查仍由运行时执行。

IDE 从这份描述生成签名、返回字段与 await 补全，移除了原先分别维护的返回／等待／字段补丁表。Computer、Device、AgentSession、Process、Team、Program、Plan、Goal、Skill 的直接调用也从同一描述族生成合同。其中 Device 的 `device(action)` 现在有与运行时相符的提示。

直接可调用协议不等于存在公开的 `.__call__` 成员；成员目录保持原来的真实属性边界。BoundTeam 的入口参数、用户函数、类／实例和动态组合仍按真实词法身份保守分析，不用一张固定签名覆盖它们。Agent 的公开配置字段继续来自已有配置 schema。描述表只描述能力，不能授予能力，也不能证明模型输出正确。

元信息完整仅表示当前原生环境已被完整描述。它不表示 Python／Lisp 的全部功能、标准库、桌面机器人或无限自我优化已经完成。编译产生机器码的 JIT 合同见[原生计算内核](NATIVE_FUNCTIONS.md)，当前支持边界见[语言核心合同](LANGUAGE_CONTRACT.md)；Python／Lisp 对照不构成待补齐的发布清单。

## 持久状态、证据与自我改进

Process 的普通 state 可以表达等待、关联问题 ID、候选方案和下一轮输入；当前没有新增一个宿主级持久 human-question 对象。程序应明确校验问题／消息身份与状态版本，回答本身不会增加原有权限。取消、异常和外部效果不确定性继续沿原 Context／Process 协议处理，不把重启当作自动重放许可。

消息关联没有丢失：宿主写入的 `process.turn.queued` 事件携带 key／messageId，并关联运行 ID，运行记录保留终态与输出。现有 SDK 的事件分页和运行查询可以重建关联。现在可用 process_message 按精确普通消息 ID 读取原始运行的可信关联结果；关联使用现有邮箱唯一键和宿主证据，不从普通 runs.input 同形数据反推来源。consumed 不能推成目标成功；历史证据不足仍返回 unavailable，协调记录不改写原运行状态。见[持久 Process](PROCESSES.md#按消息-id-读取结果)。

记忆是项目级键值数据。[std/memory](STDLIB.md#stdmemory版本化共享记忆)的普通函数通过原工具效果读取版本并按版本原子提交；旧memory.put也递增版本，两个Bot读取同一版本后只有一个CAS可以成功，冲突返回当前数据。升级前已有键的初始版本也可以是0，存在性由exists区分。后台反思可组合为有界Process，用户函数先计算候选，再显式提交；没有锁内Agent回调或自动重试。该键事务不与Process根检查点或外部动作一起提交，失败后不回滚已提交记忆，仍保留原不确定效果协调。默认语义整理与自动保留策略尚未实现。

修改自身程序继续使用 [Program 候选测试](SELF_PROGRAMMING.md)及[Process 原子演进](PROCESS_EVOLUTION.md)：反思产生候选，测试验证明确案例，CAS 检查当前来源与版本，采用后未来轮次使用新定义。活栈迁移、任意优化正确性证明和多副本分布式执行不在当前范围。

## 环境控制的验证边界

[Computer](COMPUTERS.md) 的隔离浏览器、最新观察和虚拟光标适合后台网页工作；[Device](ROBOT_DEVICES.md) 的外接 HID 会作用于目标系统的输入焦点。两者共享函数组合、Task 与权限规则，但回执证据不同。键鼠 ACK、内核提交、视觉观察、任务 verifier 分别证明不同阶段，不能互相替代。

采用新视觉模型、车辆或摄像头适配器时，应先定义观察／动作／回执合同，接入可信 Registry 和原效果层，并验证取消、租约、数据边界与无界面运行。GUI 再投影这些合同；增加一个网页按钮不等于增加一种语言能力。

[身份控制提示](CONTROL_PROMPTS.md)提供语言级最近目标中止、动态出口与清理，可组合真实普通函数、AgentTool 和 Team。控制目标来自原生身份与活动范围；研究项目名称、消息内容或模型提示文本不会自动触发这些语言操作。

## 0.106 OpenBot 串口适配边界

[原生 Robot](NATIVE_ROBOTS.md)以固定提交的 firmware/openbot/openbot.ino 与 Android Vehicle.java 核对真实串口控制／遥测协议，可信宿主实现车辆 profile、有限心跳及 TTL watchdog。Robot／Observation／Command／Receipt、函数策略、Team、AgentTool 和异步流沿原语言管线；OpenBot sent／stopSent 并非关联固件 ACK，targetVerified=false。Android 摄像头、视觉模型、导航与物理车辆验收仍未完成，不将软件适配测试写成现实运动成功。

运行中引导由原生 guidance_receive 与 progress_emit 接入同一调度器，详见[智能体语言](AGENT_LANGUAGE.md#运行中的-bot-引导与公开进度)。公开流展示计划、工作与结果摘要；隐藏模型推理不导出。


## 0.108 原生 Bot

原生 Bot 的完整规划／执行／验证、指导采用、决策证据、提交与拥有流清理同样迁入真实类型化帧，复用原 Invocation、TaskGroup、效果准入及 Process 事务。待决提案、决策身份、结果／错误和关闭阶段由字段驱动；单个等待／流关闭仍沿原叶协议执行。参数、权限、宿主事件和跨轮持久化合同不变，没有提供活栈恢复、默认模型认知或可复制 continuation。详见[Bot 执行状态](EXECUTION_FRAMES.md)。

[原生 Bot](NATIVE_BOTS.md)把真实回调、goal/context 和共享 busy／协调状态纳入原语言身份；不是应用层专用 JSON wrapper。普通 call／await、std/bot 别名、Code／Program／IR、IDE／LSP 和现有源码／图运行使用同一管线。明确传入 planner、execute、verify，默认模型认知和自动语义记忆整理仍未实现。

已接受指导的 revision 与原生决策效果准入在宿主同一锁下比较；绑定 AgentTool／Team／Program 的子效果携带原决策身份。只有执行主体尚未进入且规划阶段没有外部动作前缀时，过期提案才可自动重新规划；规划回调已开始的工具／Program 等动作同样计入前缀，任何 tool:* 即使声明 effect="pure" 也保守处理。有规划前缀或已进入执行主体时，不自动重放，保留已完成／不确定工作并要求显式协调。这个合同不把真实物理操作、模型请求或已发出的工具撤销，OpenBot 的 targetVerified=false 及视觉／导航缺口保持原样。


终态提交也在宿主同一锁下校验指导 revision，并封闭当次 Bot scope；直到 scope 关闭，新的指导明确拒绝。已经接受的相同 messageId 与相同内容仍返回原幂等回执，不再入队。非终态上下文提交不封闭 scope，之后接受的新指导仍可令旧决策过期。这个短暂封闭只保护当次终态提交，不把整个运行、持久目标或已发生的效果变成可撤销事务。


## 0.109 指定 OpenDot 研究与持久指导

本轮指定 `defog-ai/opendot`，与先前 CopilotKit/OpenDots 是不同项目。研究固定提交的 how-it-works、orchestrator 动作路径、Store 领取／动作日志与 extensions 回调；模型只提案，实际目标与凭据由宿主控制。该提交的实际动作代码／说明不提供 README 旧 tagline 所暗示的逐动作人工审批，不能据此声称上游具有审批保证。

Chaos 的映射是语言设计判断：真实回调沿原 Context／Registry 准入，Process 保存有界指导身份与检查点，显式程序发布公开计划／动作结果。baseURL／key 的原持久配置不变，不移植上游整套 Docker、订阅 CLI 登录或 Slack。持久化不等于恢复回调／Task／资源，也不提供默认语义规划器；见[持久 Process](PROCESSES.md)。

具名移动设备、Mobile MCP 配置与权限边界见[移动设备与跨设备函数](MOBILE_COMPUTERS.md)。


## 原生调用树与收件箱

Bot 调用通过真实决策祖先链建立作用域树，复用原 TaskGroup、预算和效果系统。默认根队列保持唯一所有者；嵌套调用以及显式 mailbox="scoped" 的独立根角色使用有界私有队列。宿主按每层活动决策与 revision 校验动作，实际动作身份只准入一次，其结果投影到祖先账目。定向指导和宿主活动列表共同服务 SDK、HTTP、CLI 与网页，不建立第二个 Bot 执行器。

此机制提供组合与隔离，不保证所有目标可以完成。Process 保留根检查点及子动作证据；显式声明的角色另有稳定 processRole 地址和持久队列，跨重启的执行栈恢复仍未实现。详见[原生 Bot](NATIVE_BOTS.md)。


持久角色的地址是 Process 实例中显式声明角色对应的 opaque roleId，临时 scopeId 只代表一次调用。角色注册表、独立消息唯一键与确认账使用原 Store 事务；提供消息与消费消息分开，根完成统一确认。offered 标记防止未绑定角色的保留消息触发无穷空转；退役和重新声明产生新身份。实时界面来自相同注册表、事件血缘和当前宿主绑定，不根据任务文本猜测关系。见[角色规范](NATIVE_BOTS.md#长期-process-的角色邮箱)。
