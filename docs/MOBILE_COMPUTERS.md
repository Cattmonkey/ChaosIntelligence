# 移动设备与原生 Computer

移动设备通过可信宿主注册为原生 `Computer`，与浏览器共用 `Observation`、`ComputerAction`、执行日志和智能体函数组合。程序选择注册名称；设备连接、认证材料和真实设备 ID 留在宿主，不在页面上另建一套机器人执行器。

本页区分已经编写的协议路径和仍需验证的真实硬件能力。模拟传输、隔离协议夹具和语言测试通过，不代表所有 Android、iOS 或桌面设备已经完成实机验证。

## 先运行一个不连接设备的示例

```chaos example=mobile-computer-cold
type_of(computer_attach("phone"))
```

结果为 `"Task"`。这只创建冷任务，不执行附接、不查找设备、不发网络请求，也不需要安装移动工具。仓库中的 `examples/mobile-computer.chaos` 保持这一安全行为。只有显式 `await` 才会进入受权限控制的效果路径。

## 由宿主选择设备

在自己的 Python 工程中配置可信 Mobile MCP 服务与设备，再创建运行环境：

```python
from chaos import Registry
from chaos.mobile_mcp import mobile_mcp_computer

registry = Registry()
registry.register_computer(mobile_mcp_computer(
    "phone",
    "http://127.0.0.1:8765/mcp",
    "HOST_SELECTED_DEVICE_ID",
    token_env="CHAOS_MOBILE_MCP_TOKEN",
))
```

上述注册是冷操作。端点和设备 ID 由工程维护者替换为已配置值；它不会安装上游工具、启动服务、枚举设备或读取令牌。宿主应自行准备可信服务、操作系统授权及设备配对。服务端首次使用某些模拟器时可能有自己的安装行为，不能把 Chaos 的冷注册理解为对服务端所有行为的保证。

创建 `Framework(registry=registry)` 后，需显式给项目授予 `computer.control`，才可 `await computer_attach("phone")`。附接返回当前执行上下文所有的资源，后续使用现有 `computer_observe`、`computer_action`、`computer_act`、`computer_close`。既有浏览器仍使用 `browser.control`、`computer_open` 和 `computer_connect`。

`ComputerSpec.actions` 声明适配器支持的动作；普通注册默认是五种浏览器动作，移动注册显式声明自己的动作子集。公开动作清单只是发现信息，不授予权限。执行时仍检查当前项目、资源和 AgentTool/Team/Bot 的权限范围；JSON、源码和模型返回值不能自行注册驱动或更换设备。

## 移动动作有自己的语义

| 动作 | Mobile MCP 语义 |
|---|---|
| `navigate` | 打开经校验的 HTTP(S) 地址。 |
| `click` | 使用本次观察的目标引用点击。优先用设备原生引用，无原生引用时才使用经过检查的逻辑坐标。 |
| `input` | 先点击目标，再向焦点追加文本；不自动清空，不等同于浏览器 `type` 的填充语义。 |
| `button` | 发送适配器明确支持的硬件按键名称。 |
| `launch` | 启动指定应用包。 |
| `swipe` | 按显式方向、距离和当前逻辑视口执行滑动。 |

移动绑定不把浏览器 `type`、`press`、`scroll` 偷换成上述动作。`input` 是点击与追加两个可能产生效果的步骤；第二步失败不撤销第一步，也不会自动重试任一步骤。用户或程序必须重新检查当前状态，再决定后续动作。

## 观察、智能与过程展示

观察给出当前资源、快照身份、有限目标引用、文本和可选图片元数据。每个动作绑定其观察；跨资源、旧观察、重复使用、未声明动作或权限撤回都会被拒绝。请求图片时必须有经过解码和预算校验的预览、模型两个变体，图像损坏不能静默降级成成功；显式 `image=false` 可只读取结构化界面。

移动适配器读取完整结构化界面的指纹，并在点击或输入前重新比较。图片捕获前后也检查界面是否变化。坐标来自设备逻辑视口，不从缩略图尺寸猜测。这里仍存在 **检查与使用之间的时间差（TOCTOU）**：操作系统可能在最后一次检查之后改变界面；这些检查降低陈旧动作风险，不构成系统级原子锁或外部任务成功证明。

普通函数可封装这些原生调用，作为显式授予 `computer.control` 的 AgentTool 交给智能体；Bot 和持久 Process 继续组合这些函数。长期目标、动态上下文与用户追加通过现有 `process_guide` 和指导回执进入原任务，不建立第二个并行调度循环。

附接、观察、动作开始、回执和失败进入同一运行的 Computer 日志与事件，保留源版本和调用位置。Web、SDK、终端可以据此显示公开工作说明、截图与结果；不把模型隐藏推理作为过程数据。

## 网络故障与设备租约

HTTP 超时、取消、响应不完整或无法匹配的回执，都可能发生在设备已经收到操作之后。适配器不自动重试副作用，也不把连接结束视为远端已经停止。动作可能已经派发而结果不明时，日志记录不确定结果。

若传输不能确认原请求已到达匹配的终态，适配器关闭会失败，宿主保留该物理资源的租约，防止同一个 Engine 中的其他运行再次操作它。此隔离是 **Engine 生命周期内的 quarantine**：它不是跨进程或重启后持久的分布式设备锁。重启不能证明旧远端任务已经结束；恢复前需在设备与服务端确认原任务状态。

同端点、同设备的注册别名共享派生资源身份；若不同端点实际连接同一物理设备，宿主必须给这些绑定配置相同的可信 `resource_id`。系统不通过网络探测来猜测别名关系。

## 当前协议与未完成边界

- 已编写的路径是可信注册 Computer、原生观察/动作、固定设备的 Mobile MCP 适配及有界 HTTP JSON/SSE 响应解码。MCP 工具错误不会当作成功。
- 当前适配器针对固定版本的无状态 HTTP MCP 服务和 `2025-11-25` 协议；远程地址要求 HTTPS，仅回环地址允许 HTTP，不跟随重定向。认证令牌只在宿主实际建立传输时从指定环境变量读取。
- 不自动安装或启动 Mobile MCP/mobilecli，不分配收费云设备，不实现 stdio 进程管理、账号聊天同步、nanoMuse 中继或任意函数闭包跨设备迁移。
- 注册 Computer 当前是运行内资源，不承诺像浏览器持久会话一样恢复远程设备状态。注册设备输入也不录制成可自动重放的 Skill。
- Android/iOS 的系统授权、无障碍或调试连接、设备版本、应用行为及实体输入结果，仍需在明确的实机环境单独验证。没有通用的静默控制或不影响其他程序的保证。

## 开源来源

本轮参考了两个固定源码版本，独立实现 Chaos 适配代码，没有复制 nanoMuse 的 GPL 代码、头像素材或应用壳：

1. [nano-muse/nanoMuse，提交 a0d727fc04eafa74694a05af8aeac3d793efbafd](https://github.com/nano-muse/nanoMuse/tree/a0d727fc04eafa74694a05af8aeac3d793efbafd)：借鉴设备本地执行、公开进度和轨迹关联。其当前许可是 [GPL-3.0-or-later](https://github.com/nano-muse/nanoMuse/blob/a0d727fc04eafa74694a05af8aeac3d793efbafd/NOTICE)，历史 MIT 标签不适用于当前源码。
2. [mobile-next/mobile-mcp，提交 d4a83fa45b9ce7551dba1d1f5e7bdf7400b1b023](https://github.com/mobile-next/mobile-mcp/tree/d4a83fa45b9ce7551dba1d1f5e7bdf7400b1b023)：核对设备、结构化元素、截图和动作协议。仓库为 [Apache-2.0](https://github.com/mobile-next/mobile-mcp/blob/d4a83fa45b9ce7551dba1d1f5e7bdf7400b1b023/LICENSE)；其二进制依赖和真实平台权限不因此自动完成验证。

## 包与命令行配置

基础包无需移动工具；需要截图时在自己的工程安装 `chaos-intelligence[mobile]`（Pillow）。MCP 服务由宿主独立准备。配置文件使用以下结构，令牌只提供环境变量名：

```json
{"computers":[{"name":"phone","transport":"mobile-mcp","endpoint":"http://127.0.0.1:8765/mcp","device":"HOST_SELECTED_DEVICE_ID","token_env":"CHAOS_MOBILE_MCP_TOKEN","timeout":15}]}
```

`chaos run main.chaos --computers computers.json` 与 `chaos serve --computers computers.json` 读取同一注册器；Python 工程也可调用 `load_computer_registry(path)`。这不替项目授予 `computer.control`。`chaos computer validate --registered` 和 SDK `validate_computer_action(spec, registered=True)` 只校验结构，不执行输入。

同一个原生资源可传给普通高阶函数，不需要新语言语法：

```chaos
async fn inspect(name: String, consume: Function) {
  async with await computer_attach(name) as computer {
    let observation = await computer_observe(computer, false);
    consume(observation_info(observation))
  }
}
export {inspect};
```

IDE 继续使用“电脑观察”面板，显示注册名称、动作子集和实际记录。由移动观察生成的源码函数要求调用方显式传入本次观察的 `target_ref`，不会把历史引用或猜测的文字定位器固化到代码。截图比例与逻辑视口不一致时隐藏坐标叠加。
