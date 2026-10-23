# 模型连接与智能体学习示例

先完成[快速开始](GETTING_STARTED.md)。本页把模型连接保存到当前项目的 `.chaos/settings.json`，再用同一个连接运行语言中的智能体、IDE 助手或后台任务。纯函数计算无需 Key。推理在显式调用时发起；已配置 Key 的运行中控制服务还会周期检测模型目录，即使没有发起推理。

## 选择入口

| 目标 | 操作 |
| --- | --- |
| 第一次连接模型 | 启动 `python3 chaos.py`，打开实际服务地址的 `/control`，编辑提供方并保存 |
| 在命令行逐项配置或自动部署 | `python3 chaos.py configure --models`，见[环境向导](DEPLOYMENT.md)；复用本页的配置文件与环境变量 |
| 多个项目重复配置 | 下载下方配置脚本，从环境变量写入每个项目自己的设置文件 |
| 随时询问使用与编程问题 | `python3 chaos.py ask '问题' --role assistant --topic getting-started`；REPL 可用 `.ask 问题` |
| 学习智能体函数 | 从本页的问答开始，再运行[五个学习示例](MODEL_EXAMPLES.md)中的多角色、并发、函数工具与 Bot |
| 将某个智能体交给另一模型 | 为该智能体显式设置 `route`，在 `agents` 中配置同名路由 |
| 查看当前账号实际可用模型 | 保存凭据后手动检测提供方；检测目录与模型推理授权是两件事 |

## 服务地址与模型

本系统的示例服务地址为 **`https://api.example.com/v1`**，通过 OpenAI Python SDK 的兼容接口连接。这是可修改的第三方端点；模型名称是配置示例，不代表已验证你账号当前拥有这些模型。完整目录应由该提供方的 `/models` 响应确认。

| 提供方 ID | 用途 | 示例模型 | Key 环境变量（供本页脚本读取） |
| --- | --- | --- | --- |
| `text` | 规划、执行、复核和语言智能体 | `gpt-6.1-sol` | `CHAOS_TEXT_KEY` |
| `image1k` | 1K 出图 | `gpt-image-2-1K` | `CHAOS_IMAGE_1K_KEY` |
| `image4k` | 4K 出图 | `image2-4k` | `CHAOS_IMAGE_4K_KEY` |
| `grok` | Grok 智能体 | `grok-4` | `CHAOS_GROK_KEY` |

默认角色是 planner → `gpt-6-astra`、executor → `gpt-6-sol`、reviewer → `gpt-6.1-sol`。这些角色路由可以独立修改，并非必须采用的任务分配策略。图像角色默认使用 `image1k`；仅填写 `image4k` Key 不会自动切换图像角色，需要在控制台把 image 角色的 provider/model 改为 `image4k`/`image2-4k`。出图需要对应模型接口和工具，普通文本 Agent 不会因为配置图像 Key 自动变成出图程序。

公开教程不附带任何账号的通用免费凭据。使用自己的 Key；本机私有学习补充可以预填部署者提供的连接值，但不应进入语言源码、示例下载、在线文档或分发包。

## 在页面中配置

1. 第一次使用完整环境，执行 `python3 chaos.py setup`，再运行 `python3 chaos.py`。
2. 打开启动输出中实际地址的 `/control`。目录 IDE 默认使用空闲端口，不一定是 8765。
3. 在提供方中填写 Base URL、API Key 和模型，然后保存。Key 留空表示保留原值，清除需用明确的清除操作。
4. 在角色配置中选择 provider/model；希望固定模型时关闭 `autoUpgrade`。保存后下一次请求读取新配置，已发出的请求不会中途替换连接。
5. 手动检测提供方模型。失败时检查该端点、账号和授权；不要把未检测到模型解释成整个语言无法运行。

提供方、角色、智能体之间的继承规则、候选切换和后台任务见 [Harness](HARNESS.md)。原服务命令 `chaos serve --config FILE` 使用显式文件；省略时使用 `CHAOS_CONFIG` 或用户级配置。独立 `chaos ask` 也遵循这个优先级，不自动发现当前目录项目。目录入口的 `run`、`repl`、`ask` 默认使用项目 `.chaos/settings.json`，显式 `--config` 可以覆盖，相对路径按项目根目录解释。`chaos project ask --directory DIR` 同样选择项目设置；IDE 和 `assist` 则使用所连接服务的设置。

## 用脚本保存到当前项目

将[配置脚本 configure_models.py](../examples/configure_models.py)下载到项目根目录，与 `chaos.py` 放在一起。脚本复用 `Settings.update` 的验证、版本检查和原子写入，不另建配置格式。它通过项目启动器的同一选择与锁机制加载当前活动运行时和匹配依赖，换版后不会退回初始 `.chaos/runtime`；也支持已安装 Chaos 后建立的目录项目。升级到含此修订的版本后，请重新下载该脚本，替换自己复制的旧示例副本。设置文件仍为项目根下的 `.chaos/settings.json`，不进入版本环境。

macOS／Linux 示例：把 `YOUR_…_KEY` 换成自己的值；也可以从仅本机读取的环境文件加载。

```sh
export CHAOS_BASE_URL='https://api.example.com/v1'
export CHAOS_TEXT_KEY='YOUR_TEXT_KEY'
export CHAOS_IMAGE_1K_KEY='YOUR_IMAGE_1K_KEY'
export CHAOS_IMAGE_4K_KEY='YOUR_IMAGE_4K_KEY'
export CHAOS_GROK_KEY='YOUR_GROK_KEY'
export CHAOS_PLANNER_MODEL='gpt-6-astra'
export CHAOS_EXECUTOR_MODEL='gpt-6-sol'
export CHAOS_REVIEWER_MODEL='gpt-6.1-sol'
python3 configure_models.py --project .
```

只使用文本时可以只提供 `CHAOS_TEXT_KEY`。省略的 Key 保持已有值；首次创建时保持未配置状态。显式设置三个角色模型会将这三个角色固定为相应模型（`autoUpgrade=false`）；未设置的角色保持当前设置。脚本不会因为重新运行就清空其他提供方、插件或自定义智能体。

Windows PowerShell 使用对应变量赋值方式：

```powershell
$env:CHAOS_BASE_URL = 'https://api.example.com/v1'
$env:CHAOS_TEXT_KEY = 'YOUR_TEXT_KEY'
$env:CHAOS_PLANNER_MODEL = 'gpt-6-astra'
py -3 configure_models.py --project .
```

以上变量是**示例配置脚本或 `configure --models` 的输入**，运行时不会自动读取这些自定义名字。脚本把它们保存后，CLI、IDE 和后台服务读取同一设置文件；终端只显示配置版本和是否有 Key。脚本不发模型请求。仅在显式添加下列选项时检测模型目录：

```sh
python3 configure_models.py --project . --discover text
```

每个提供方可分别设置 `CHAOS_TEXT_BASE_URL`、`CHAOS_IMAGE_1K_BASE_URL`、`CHAOS_IMAGE_4K_BASE_URL`、`CHAOS_GROK_BASE_URL`；这些值优先于共享 `CHAOS_BASE_URL`。对应的 `_MODEL` 变量设置提供方默认模型；角色自身指定的模型仍有更高优先级。

兼容变量 `CHAOS_MODEL_URL`、`CHAOS_MODEL_KEY`、`CHAOS_MODEL_NAME` 是临时模型路由入口。URL 非空时，Settings 在每次 resolve 的副本中覆盖 text 连接与文本角色模型；普通／绑定 Agent、Session、生成式 UI 与 IDE／后台任务共用这条规则，更具体的智能体覆盖继续优先。临时值不修改持久配置、插件或宿主自定义客户端，移除变量后立即恢复保存的路由；公开配置页和模型发现仍针对保存值。为了明确区分临时运行和持久配置，本教程脚本及部署向导仍拒绝混用：POSIX 可先执行 `unset CHAOS_MODEL_URL CHAOS_MODEL_KEY CHAOS_MODEL_NAME`，PowerShell 可移除对应 `Env:` 项。`OPENAI_API_KEY` 不会自动代替本系统提供方配置。

## 第一个真实模型智能体

保存为 `main.chaos`，也可下载[完整源码](../examples/learning-models/main.chaos)：

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

```sh
python3 chaos.py check
python3 chaos.py run --input-json '"闭包是什么？"'
```

`check` 不调用模型；`run` 会实际请求已配置模型，输出内容不保证固定。`teacher` 是可调用的语言值，可以传给函数、放入列表、与其他函数组合。`tools: []` 表示没有授予宿主工具，不代表无限电脑控制权限。要让模型调用自己的函数，阅读[函数工具](FUNCTION_TOOLS.md)；要离线验证工具执行，阅读[智能体协议测试](AGENT_TESTING.md)。

完成第一次问答后，进入[大模型智能体：五个可运行示例](MODEL_EXAMPLES.md)。后续示例继续使用本页的项目配置，无需再次填写 Key。

## 按智能体覆盖连接

上例的 `route: "learning-teacher"` 显式选择 `agents["learning-teacher"]`。没有该项时仍使用 planner 角色。控制台可新增此路由，指定 provider、model、baseURL 或 apiKey。Python 宿主也可使用同一个公开配置接口（先在该 Python 环境安装 Chaos）：

```python
import os
from chaos.settings import Settings

settings = Settings("/absolute/path/my-project/.chaos/settings.json")
current = settings.public()
settings.update({
    "agents": {
        "learning-teacher": {
            "role": "planner",
            "provider": "text",
            "model": "gpt-6.1-sol",
            "autoUpgrade": False,
            "baseURL": "https://api.example.com/v1",
            "apiKey": os.environ["CHAOS_TEXT_KEY"],
        }
    }
}, version=current["version"])
```

如果只想改变模型，省略路由中的 baseURL/apiKey，继续继承提供方连接。共享 Key、每框架分配 Key、按智能体分配 Key 都使用同一继承机制。文件中的 `version` 是并发修改检查，不是运行时发布版本。不要打印 `settings.resolve()` 的结果，其中包含供客户端使用的真实凭据；检查配置应使用脱敏的 `settings.public()`。

## 学习、测试与生产的边界

| 示例类型 | 能验证什么 | 不能证明什么 |
| --- | --- | --- |
| `fn`、递归、模块、异步任务等纯语言示例 | 当前解释器的确定性计算 | 模型或设备服务是否在线 |
| `local` 提供方或显式 fixtures | 语言与工具协议、能力和错误路径 | 大模型推理质量 |
| 本页真实模型 Agent | 当前服务与账号的一次实际调用 | 所有模型始终可用、任务一定完成 |
| Robot／Computer 示例 | 配置、观察和动作接口的组合 | 未连接硬件上的真实效果 |

长期目标用 [Bot](NATIVE_BOTS.md) 的显式回调，再按需要组合 [Process](PROCESSES.md) 与 [Session](SESSIONS.md)；可视化展示程序公开事件和工作状态。模型配置不会赋予额外权限，也不改变这些语言合同。

## 复用连接提供内置帮助

`std/intelligence` 的 planner／executor／reviewer 使用对应角色；assistant 采用 executor 角色，独立 route 为 `intelligence-assistant`。因此同一提供方连接既能用于上面的自定义 Agent，也能用于 `ask`，无需再填一套 Key。需要为帮助智能体单独选模型时，按上述 `agents` 路由覆盖方法配置它。

`python3 chaos.py docs list` 列出随包主题，`docs show TOPIC_ID` 可离线阅读；`ask '问题' --topic TOPIC_ID` 才将所选文档作为上下文发送给模型。它不自动发送项目文件，也不执行回复。让智能体读取项目、提出和验证代码改动，应连接现有 IDE 服务使用 `assist`；完整例子和配置边界见[命令行教程](COMMAND_LINE.md)。
