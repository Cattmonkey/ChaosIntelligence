# 机器人组合与外接 HID 设备

机器人是可组合的语言程序。普通函数组织行为，Agent／AgentTool 处理模型决策与受控工具，Team 限制角色权限，AgentSession 保留会话，Process 驱动持久轮次，Program 保存、测试并采用行为版本。外接 Device 将键盘和鼠标效果纳入同一个 Task 调度器。源码、IR、SDK、CLI 和工作台使用相同语义，不另设机器人解释器或调度器。

## 明确收件人和角色

下面的例子只运行本地函数，没有模型请求或设备输入。它也保存在 `examples/robot-composition.chaos`，可用原 CLI 直接运行。

```chaos example=robot-structured-routing
let team = team_from({kind: "team", name: "robot", entry: "main", roles: [
  {id: "main", parent: null, capabilities: [], agent: {kind: "agent", name: "main", config: {provider: "local", tools: []}}},
  {id: "worker", parent: "main", capabilities: [], agent: {kind: "agent", name: "worker", config: {provider: "local", tools: []}}}
]});
let workers = team_bind(team, {worker: fn(value: Int) -> Int { value + 2 }});
async fn dispatch(message: Record) {
  let role = team_role(workers, message.to);
  await role(message.value)
};
let robot = team_bind(team, {main: dispatch});
let inbox = channel(1);
await channel_send(inbox, {to: "worker", value: 40, text: "@someone [PASS]"});
channel_close(inbox);
let message = await channel_receive(inbox);
await robot(message.value)
```

结果为 `42`。`to` 是程序明确读取的角色 ID；文本中的 `@someone` 和 `[PASS]` 没有控制意义。主角色通过实际绑定的 callable 委派工作，名字 `main`、提示词或展示层级均不会授予权限。未知角色由既有 Team 校验拒绝；角色能力始终与项目及调用者能力取交集。普通派发函数可以登记为 AgentTool，让模型调用正式工具参数，而不从自由文本猜测命令。

## 持续身份、群聊与记忆

| 需求 | 复用的语言机制 | 边界 |
|---|---|---|
| 有身份的对话机器人 | Agent 定义与命名 AgentSession | 会话保存公开消息和工具证据，不保存模型私有推理或活调用栈 |
| 群成员和主角色 | Team、team_bind、普通函数策略 | 对话成员关系与工具授权分别检查；主角色没有隐式管理员特权 |
| 同轮通信 | Channel、Task、TaskGroup | 有界、同一运行；不能把 Channel 存入持久进程状态 |
| 跨轮消息与主动运行 | Process 邮箱、定时间隔、JSON state | 邮箱在下一轮处理；不是向任意执行中的表达式注入消息 |
| 私聊与群聊隔离 | 不同 Session key、显式共享摘录 | 不自动复制私聊到群上下文；读取权限仍遵守 Session 合同 |
| 可追溯记忆 | Process state、既有记忆工具、消息来源与修订数据 | 不把模型生成的摘要自动视为已验证事实 |
| 自我修改 | Program probe／adopt 或 ProcessEvolution | 必须显式测试与 CAS 采用，不修改正在运行的快照，不保证自我改进必然成功 |
| 能力启停 | 可信宿主 PluginHost 与 AgentTool／Team 授权 | 依赖重建和清理复用现有宿主，不直接加载第三方 DSH JavaScript 插件 |

不同 Session key 隔离默认模型上下文，不自动建立每机器人独立的数据库 ACL。授予角色会话管理或历史读取能力前，应按现有 Session 权限合同审查其可访问范围；只传显式摘录的群策略本身是普通程序。

持续机器人应把会话 key、设备注册名、目标和必要业务数据保存到 Process state，每轮重新取得活资源。Process 与 AgentSession 都有消息去重和修订检查；不确定外部效果需要 reconciliation，不能因重启自动重发。Session 的 `compact` 保留选定的完整轮次作为上下文；当前接口不等同于自动语义摘要服务。群聊轮次策略、摘要提案和记忆提案可以由普通函数及智能体组合，但本页不声称内置了完整的群聊应用、自动上下文 steward 或日历 cron。

详见[持续进程](PROCESSES.md)、[持久会话](SESSIONS.md)、[函数角色团队](TEAM_FUNCTIONS.md)、[原子进程演化](PROCESS_EVOLUTION.md)和[目标／计划／技能](AGENT_MODES.md)。

## Device 是受控资源

Device 由可信宿主以名字注册；语言只选择名字，不能传入串口路径、导入模块或注册任意驱动。项目必须显式授予保留能力 `device.control`；Agent 和 Team 还受自身能力上限限制。普通安装和纯语言计算不需要硬件或串口依赖。

| 接口 | 行为 |
|---|---|
| `device_open(name)` | 创建冷 Task；await 时租用已注册设备，得到 Device |
| `device_info(device)` | 返回当前租约的 `id/name/generation/status/profile` |
| `device_action(device, spec)` | 纯验证并构造绑定当前租约的 DeviceAction，无 I/O |
| `device_action_info(action)` | 返回 `deviceId/generation/spec/used` |
| `device_act(device, action)` 或 `device(action)` | 创建冷 Task；await 时检查权限并消费一次 action |
| `device_receipt_info(receipt)` | 返回有界回执数据；ACK 不代表目标任务已成功 |
| `device_close(device)` | 创建关闭 Task；await 执行清理，可重复关闭 |

Device、DeviceAction、DeviceReceipt 都受解释器及执行周期所有权检查。Action 绑定精确租约与代次，不能换设备、重定向到新租约或在承认不确定失败后再次使用。别名共享一次性消费状态；即使装入容器、闭包或团队返回值也不增加权限。JSON 只提供诊断描述，不能恢复活资源。

使用 `async with await device_open("desk") as device { ... }` 让现有异步资源协议管理退出；普通 `with` 不能驱动设备效果。构造冷 Task 不发送输入，源代码中的实参求值仍按普通语言顺序执行。退出和取消沿有界清理路径释放设备；关闭派发不会撤销已经进入目标电脑的输入。持久 Process、Program 定义以及跨轮 JSON state 不保存 Device 句柄。

设备动作采用显式 HID usage 和报告字段，不做自然语言命令或键盘布局猜测。当前录制 scope 拒绝设备效果并中止该录制，不能悄悄遗漏硬件输入后声称得到可重放 Skill。Program 候选测试应使用无硬件的函数或受控假设备，不以真实键鼠动作测试自我修改。

## 安装、注册与调用

取得维护者提供的完整 wheel，或按[安装与构建](INSTALLATION.md)从源码生成 wheel，核对随附校验值。设备宿主按需执行 `python -m pip install './dist/chaos_intelligence-0.99.0-py3-none-any.whl[devices]'`，将路径和版本替换为实际取得的本地文件，再注册已接好的串口桥。完整源码包、wheel、可复制包与 Python SDK 的使用分别见[安装指南](INSTALLATION.md)及[嵌入 SDK](SDK.md)。驱动实现由可信宿主提供，安装包本身不会连接任何设备。

```python
from chaos import Framework, Registry, serial_device
registry = Registry()
registry.register_device(serial_device("desk", "/dev/cu.usbserial-example"))
with Framework(registry=registry) as app:
    app.create_project("robot", source="input", capabilities=["device.control"])
    # 通过原项目源码执行入口提交下方程序。
```

`serial_device` 的默认速率为 115200，超时为 2 秒；`DeviceSpec` 也可注册受信任的专用适配器。服务可通过 `make_server(..., registry=registry)` 使用同一注册表。CLI 的 `chaos serve --devices devices.json` 加载严格的设备清单，项目仍通过原 UI／SDK 显式授予能力。单次执行使用 `chaos run robot.chaos --devices devices.json --capability device.control`；注册与授权是两个独立步骤。也可用公开 `load_device_registry(path)`。文件形状如下，`baudrate` 与 `timeout` 可省略：

```json
{"devices":[{"name":"desk","port":"/dev/cu.usbserial-example","baudrate":115200,"timeout":2}]}
```

下面的语言程序只应在注册好目标设备并授予能力后执行；它不属于默认无硬件示例检查：

```text
async with await device_open("desk") as device {
  let action = device_action(device, {action: "mouse_move", x: 10, y: 0});
  let receipt = await device(action);
  device_receipt_info(receipt)
}
```

| `spec.action` | 精确参数 |
|---|---|
| `key_tap` | `keys`: 1–6 个互不重复的整数 HID usage，范围 1–231；可选 `modifiers`: 0–255，默认 0 |
| `mouse_move` | `x/y`: -127–127 的整数相对位移；可选 `wheel` 同范围，默认 0 |
| `mouse_click` | `button`: 1、2、4、8 或 16 中一个值 |
| `release` | 仅 `action` 字段，释放所有键／按钮 |

额外字段、Bool 冒充 Int、越界值及未知动作会被拒绝。主机和固件使用同一 `chaos/hid_protocol.py` schema。`device.control` 不在默认授予集合内；注册设备也不会自动给项目授权。租约排他按同一个 Engine 内的物理资源标识检查，设备别名不能绕过；不能把它夸大为跨机器的分布式锁。

桥接固件位于 `examples/hid_bridge/`：将 `boot.py`、`code.py`、`hid_bridge.py` 和安装源码中的**同一份** `chaos/hid_protocol.py`（在板上名为 `hid_protocol.py`）复制到支持 USB HID 的 CircuitPython 板。Pico 的 USB 接目标电脑；控制端使用独立 USB-UART，TX 接 GP1、RX 接 GP0、GND 共地，使用 3.3V 电平。不要把控制 UART 与目标 USB 口混作同一个输入通路。具体板型、CircuitPython USB 配置及布线需按设备实际条件核验；软件测试没有代替这一步。原生接口依据 CircuitPython 官方 [usb_hid](https://docs.circuitpython.org/en/latest/shared-bindings/usb_hid/index.html) 和 [busio.UART](https://docs.circuitpython.org/en/latest/shared-bindings/busio/index.html#busio.UART) 文档。

## 树莓派 Linux USB gadget

树莓派 4B／5 使用板载 USB-C peripheral 控制器，可直接运行 Chaos 并通过 Linux `/dev/hidg*` 节点发送同一组键鼠动作。它与 Pico／CircuitPython 是不同部署方式，语言中的 Device 接口保持不变。新增可信宿主工厂 `linux_hid_device`，设备清单用 `transport: "linux-hid"` 和实际键盘／鼠标节点配置；无 `transport` 的旧串口清单保持兼容。Linux 后端的 ACK 仅证明报告提交到内核队列，仍保持 `targetVerified=false`。部署检查、描述符、启停、供电和跨系统边界见[树莓派 4B／5](RASPBERRY_PI.md)。

## 外接设备与证据

物理拓扑是控制端向桥接固件发送串口协议，桥接板通过 USB Device 接口向目标电脑呈现键盘／鼠标。USB 主机向现成键盘写一个输出报告，不等于向另一台电脑注入键盘输入。桥接板需具备适合固件的 USB HID 与控制 UART；设备选择由宿主配置，不由模型猜测。

串口请求与 ACK 使用连接身份和请求身份关联，限制帧、收发规模和绝对截止时间。一次输入进入发送阶段后不自动重试；超时、掉线或取消可能意味着目标已收到部分输入，因此标记不确定并停止普通动作，需重新建立经过协调的安全会话。关闭成功后可以显式重新 open；如果关闭失败，Engine 继续隔离该物理端点，不把它重新交给其他租约，也不自动重试输入。释放键／按钮和固件 watchdog 是尽力措施，软件不能在物理断电后保证补发释放。

串口后端的 `acknowledged` 仅表示桥接端处理了指定的有界报告序列；`targetVerified=false` 明确未核验目标应用状态。确定性假驱动、串口模拟和固件模拟证明软件合同，不是物理 USB 端到端验收。当前没有在本任务中对真实目标电脑注入输入。

物理 HID 通常作用于目标系统的前台或全局输入，不能承诺不影响那台电脑的其他程序。隔离 Chromium 的虚拟光标则属于[Computer](COMPUTERS.md) 后端，不移动本机系统指针；两种能力不可混称。

## 借鉴来源与实际差异

用户确认的项目 [Fei2411/DeepSeek-Bot](https://github.com/Fei2411/DeepSeek-Bot/tree/ae1d10591aae19192c2c53e6fb4133534fc45aa6) 是独立社区的 DSH 插件，不是官方 DeepSeek 产品；用户提供的 `FeiZhuLulu/DeepSeek-Bot` 地址重定向至此，项目身份已确认。固定研究提交为 `ae1d10591aae19192c2c53e6fb4133534fc45aa6`，本功能没有复制其源码。

其 [roster](https://github.com/Fei2411/DeepSeek-Bot/blob/ae1d10591aae19192c2c53e6fb4133534fc45aa6/ds-bot/src/host/roster.js) 维护长期 Bot 身份与主角色，[groups](https://github.com/Fei2411/DeepSeek-Bot/blob/ae1d10591aae19192c2c53e6fb4133534fc45aa6/ds-bot/src/host/groups.js) 为每名成员建立群会话，[capabilities](https://github.com/Fei2411/DeepSeek-Bot/blob/ae1d10591aae19192c2c53e6fb4133534fc45aa6/ds-bot/src/host/capabilities.js) 按配置身份卸载／挂载能力；这些思路分别对应语言的身份、隔离和资源生命周期。

源码也揭示重要边界：群路由使用 `@` 与 PASS 文本；首次群会话通过 [ownChatBrief](https://github.com/Fei2411/DeepSeek-Bot/blob/ae1d10591aae19192c2c53e6fb4133534fc45aa6/ds-bot/src/host/chat-index.js#L282) 自动带入最近私聊，尽管 README 的隔离表述更强。Chaos 用明确参数和授权摘录组合行为，不把提示词当权限边界。其运行时能力可动态挂载，但修改宿主插件代码仍需重启 DSH；这不等同于无边界热替换活程序。现有 [Harness](HARNESS.md) 对 DeepSeek Harness／Cordis 的适配是单独且已固定版本的 Python 生命周期实现，并不提供其整个 JavaScript 插件 ABI。

## 原生车辆与 profile 通道

[Robot／观察／命令](NATIVE_ROBOTS.md)将车辆资源、带单位单包遥测、基于原观察时间的一次性命令与 OpenBot 串口传输接入原 Task／函数／Team 管线。Robot 使用独立保留语言权限 robot.control；HID 的 device.control、旧接口和 ACK 合同保持原样。sent／stopSent 及 targetVerified=false 不等于车辆执行成功，Android 摄像头／视觉和物理验收仍需后续实现。
