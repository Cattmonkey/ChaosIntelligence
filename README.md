# Chaos Intelligence

## 把智能体当函数，把想法写成程序。

**Chaos 是一门以函数为基础、把智能体作为可调用值的编程语言，也是一套可以放进项目目录的开发环境。** 普通函数负责计算和流程，智能体负责理解与生成；两者写在同一个程序里，传递数据、组合任务、返回结果。

同一份 `.chaos` 源码，可以在终端运行、在网页 IDE 中编辑，也可以通过 SDK 嵌入 Python 工程。

[**下载最新使用包**](https://github.com/Cattmonkey/ChaosIntelligence/releases/latest) · [**十分钟开始**](docs/GETTING_STARTED.md) · [**大模型示例**](docs/MODEL_EXAMPLES.md) · [**文档中心**](docs/README.md) · [**支持项目**](#支持项目)

下载页请选择 **`Chaos-Project.zip`**。它含有项目启动器；GitHub 自动生成的 “Source code” 归档用于源码开发。

**编写和运行 Chaos 程序，使用语言自己的 `chaos` 命令。** 已安装的环境直接执行：

```sh
chaos -c '40 + 2'
chaos run main.chaos
chaos repl
```

免安装的 project ZIP 在 macOS／Linux 使用 `./chaos`，Windows PowerShell 使用 `.\chaos.cmd`。源码仓库使用 `./bin/chaos`；在源码根目录执行 `export PATH="$PWD/bin:$PATH"` 后，当前终端也可直接使用 `chaos`。

当前运行时需要 Python 3.9+，命令入口会寻找可用解释器；日常编程不用写 Python 命令。需要显式选择解释器时，目录包仍支持 `python3 chaos.py`，源码支持 `python3 -m chaos`。旧版目录包没有 `chaos`／`chaos.cmd` 时也可继续用原入口。下文目录操作面向 project ZIP；源码／已安装 CLI 的项目管理使用 `chaos project --help`。[安装与兼容入口](docs/INSTALLATION.md)

### 用它做什么？

- **把一次问答变成可复用的工作流程。** 让规划、写作、复核各司其职，用普通函数连接输入和结果；把精确计算交给代码，把需要理解的部分交给模型。
- **让程序直接产出页面、报告和演示。** 计算得到的数据可以变成表格、图表、幻灯片；运行记录可以变成可交互的状态网络，不必先搭建另一套前端应用。
- **让机器人围绕目标持续协作。** 用 Bot 组合计划、执行与验收，在接收点追加指导，再通过持久会话与进程保留长期工作状态。

这些能力共享语言、项目配置与工具权限。换模型、增加角色或接入设备时，可以继续组合已有函数与模块。[了解语言设计与支持范围 →](docs/LANGUAGE_CONTRACT.md)

## 2 分 35 秒，认识 Chaos Intelligence

**让智能，成为语言。** 从可组合的智能体，到围绕目标工作的 Bot，再到可交互的报告与实时运行网络——用一支有配音的短片，了解这门语言能帮助你构建什么。

https://github.com/user-attachments/assets/2aa53c85-8d36-4e21-8322-2bdc9188f17a

[下载 1080p 完整宣传片](https://github.com/Cattmonkey/ChaosIntelligence/releases/download/v0.154.4/Chaos-Intelligence-Brand-Film-1080p.mp4) · [中文字幕](https://github.com/Cattmonkey/ChaosIntelligence/releases/download/v0.154.4/captions.zh-CN.srt) · [宣传封面与素材说明](docs/assets/brand-film/README.md)

影片包含中文旁白、原创配乐、真实 IDE／报告／运行网络界面和标注的能力示意。真实界面中的业务数字为演示输入；设备与 GPU 能力按配置、授权和支持范围启用。

## 先看看它怎样工作

**用 40 秒走一遍：编辑代码 → 运行 → 生成看板 → 查看运行网络。** 视频配有中文步骤字幕，从实际效果开始，看完即可按下面的源码复现。

[![播放 Chaos 40 秒操作演示：从代码到可交互的结果](docs/assets/showcase/overview.png)](https://raw.githubusercontent.com/Cattmonkey/ChaosIntelligence/main/docs/assets/showcase/overview.mp4)

[▶ 播放／下载完整演示（MP4，40 秒）](https://raw.githubusercontent.com/Cattmonkey/ChaosIntelligence/main/docs/assets/showcase/overview.mp4) · [跟着演示操作](examples/showcase/README.md)

`00:00` 编写与排版 · `00:08` 运行与事件 · `00:15` 指标、图表与排序 · `00:26` 网络与节点详情

### 写代码 → 格式化 → 运行 → 看结果

![Chaos IDE：格式化源码、保存并运行，查看结果和公开执行事件](docs/assets/showcase/ide-workflow.gif)

左边管理文件，中间编写源码，底部看结果。语义配色、名称补全、参数提示和保存时格式化使用同一语言分析服务。需要帮助时打开“与助手编程”，检查修改提案后再应用。

[查看静态截图](docs/assets/showcase/ide-workflow.png) · [照着动图操作](examples/showcase/README.md) · [IDE 使用指南](docs/IDE.md)

### 让程序展示自己的数据与运行状态

![Chaos 生成式界面：报告、图表与真实运行网络](docs/assets/showcase/live-dashboard.gif)

页面和报告是程序返回的值。OpenUI 组件负责展示，`RuntimeGraph` 读取同一项目的真实结构与运行记录；节点详情可区分声明关系和实际观测。页面收到运行事件后更新状态。

以上视频与动图由 v0.154.3 的真实操作画面剪辑而成，添加步骤字幕并调整停顿以便阅读。示例无需模型 Key；报告中的业务数字是标明的演示数据，网络读取实际运行记录。模型生成内容需要另行配置，公开事件不包含模型内部推理。

[查看静态截图](docs/assets/showcase/live-dashboard.png) · [运行同款报告源码](examples/showcase/live-report.chaos) · [生成页面与报告](docs/GENERATIVE_UI.md) · [实时网络页面](docs/LIVE_NETWORK_UI.md)

## 三个小例子，看懂这门语言

### 1. 普通计算，先从 42 开始

```chaos
fn twice(f: Function, x) { f(f(x)) }
fn add_one(x: Int) -> Int { x + 1 }

twice(add_one, 40)
```

结果是 `42`。函数可以作为参数传递；最后一个表达式就是程序结果。把代码保存为 `hello.chaos`，执行 `./chaos run hello.chaos` 即可。**纯计算无需网络，也无需模型密钥。** [继续学习函数组合 →](docs/FUNCTION_COMPOSITION.md)

### 2. 调用智能体，就像调用函数

```chaos
agent teacher {
  provider: "model",
  role: "planner",
  prompt: "用一个短例子解释输入中的编程概念。",
  tools: [],
  maxSteps: 3
}

teacher("闭包是什么？")
```

配置完成后，运行这段程序会调用真实模型。先在项目的 `/control` 或 `./chaos configure --models` 中保存 Base URL、Key 和模型选择；`role` 继承项目中的角色配置，源码无需写入密钥。真实调用可能产生供应商费用。

声明智能体不会发起请求，最后一行的调用才会执行。`tools: []` 表示本例没有授予工具权限。项目中的 `local` 提供方用于确定性演示，真实语义推理使用这里的 `model`。[配置模型 →](docs/MODEL_CONFIGURATION.md) · [完整问答源码 →](examples/learning-models/main.chaos)

同一种调用方式，还能组合出更完整的程序：

| 想做的事 | 可运行示例 | 学到的能力 |
| --- | --- | --- |
| 规划 → 写作 → 复核 | [多角色流水线](examples/learning-models/pipeline.chaos) | 将前一步的结果明确交给下一步 |
| 同时获得不同视角 | [并发分析](examples/learning-models/parallel.chaos) | `async fn`、`await` 和任务汇合 |
| 让模型解释准确的预算 | [函数工具](examples/learning-models/function-tools.chaos) | 函数算数、模型解释，并检查工具调用证据 |
| 围绕目标迭代修改 | [目标 Bot](examples/learning-models/goal-bot.chaos) | 上下文、完成条件与有界工作轮次 |

按[五个大模型示例教程](docs/MODEL_EXAMPLES.md)复制源码、准备输入，再逐个运行；模型给出的复核意见仍需结合明确的程序验收。

### 3. 程序可以返回一个实时页面

```chaos
ui_artifact("page", "系统实时网络",
  ui_node("RuntimeGraph", [
    "真实运行状态", "radial", "project", "auto"
  ])
)
```

在 IDE 中保存为入口并运行，在“页面 / 报告”中查看自己的项目网络。它是一个普通语言产物，可以从函数返回、由智能体生成，也可以嵌入更大的报告。实时绑定由在线宿主提供；离线导出会明确显示未连接。

[完整网络示例](examples/live-network.chaos) · [表格报告](examples/ui-report.chaos) · [自动翻页演示](examples/ui-slides.chaos) · [给运行追加指导](docs/REALTIME.md#从运行到追加指导)

## 三步开始，在自己的目录里用

需要 **Python 3.9+**。macOS／Linux 使用 `./chaos`；Windows PowerShell 将以下命令中的 `./chaos` 换成 `.\chaos.cmd`。

1. 从[最新发行版](https://github.com/Cattmonkey/ChaosIntelligence/releases/latest)下载 **`Chaos-Project.zip`**，解压到一个新目录，在该目录打开终端。
2. 运行自带程序，确认得到 `42`：

   ```sh
   ./chaos run
   ```

3. 准备 IDE 依赖并打开当前目录：

   ```sh
   ./chaos setup --yes
   ./chaos
   ```

`setup` 检测系统、在项目内安装依赖、运行自检并保存配置，命令行显示阶段与结果。它需要可访问的 Python 包来源；包本身不包含 Python。后续启动通常只需 `./chaos`，按终端打印的实际地址使用网页。

进入 IDE 后：选择 `main.chaos` → 编辑 → 格式化／保存 → 点击“运行” → 查看底部结果与事件。选择其他文件只改变编辑对象，默认运行入口仍由项目设置决定。[跟着快速开始操作 →](docs/GETTING_STARTED.md)

已有工程也能使用：从解压后的使用包运行 `./chaos install "/absolute/path/my-project"`，安装器遇到冲突会保留原内容。仅需要语言核心可运行 `setup --yes --profile core`。[安装说明](docs/INSTALLATION.md) · [环境检测与配置](docs/DEPLOYMENT.md)

### 喜欢终端？可以全程不用网页

```sh
./chaos -c '40 + 2'
./chaos repl --empty
./chaos docs show getting-started
```

配置模型后，还可以带着本版本文档提问：

```sh
./chaos ask '给我一个函数组合的短例子' --topic getting-started --stream
```

`ask` 返回回答，不修改文件；IDE 助手或 `assist` 可以生成需要检查与应用的代码提案。自己的程序也可导入 `std/intelligence`，组合内置规划、执行、复核与助手。[命令行教程 →](docs/COMMAND_LINE.md)

## 从小程序到更复杂的系统

| 能力 | 可以怎样组合 | 深入了解 |
| --- | --- | --- |
| 可复用语言核心 | 函数、闭包、模块、模式、宏、生成器与异步任务 | [语言参考](docs/LANGUAGE.md) · [标准库](docs/STDLIB.md) |
| 持续 Bot | 将目标、计划、执行与验收写成函数；用 Session／Process 持久化长期工作 | [原生 Bot](docs/NATIVE_BOTS.md) · [持久机器人示例](examples/durable-bot/main.chaos) |
| 实时协作 | 观察公开事件，在程序接收点追加指导，显式停止运行 | [实时交互](docs/REALTIME.md) |
| 工具与设备 | 注册并授权函数工具、电脑和移动设备能力，在同一程序中调用 | [函数工具](docs/FUNCTION_TOOLS.md) · [电脑](docs/COMPUTERS.md) · [手机](docs/MOBILE_COMPUTERS.md) · [机器人与 HID](docs/ROBOT_DEVICES.md) |
| GPU 数值计算 | 支持范围内的函数使用 CPU／CUDA／Apple M 系列 MPS 后端；MPS 可选融合 Metal 内核 | [GPU 计算](docs/GPU_COMPUTE.md) · [Metal](docs/GPU_METAL.md) · [控制流范围](docs/GPU_CONTROL_FLOW.md) |
| 嵌入已有工程 | Python SDK 与 HTTP 接口运行同一语言、订阅同一服务的事件 | [SDK](docs/SDK.md) · [HTTP API](docs/HTTP_API.md) |

GPU、设备和工具需要对应依赖、硬件与授权；它们不因写入提示词就自动可用。当前实机验收以 macOS、Python 3.9／3.13 为基础；Windows／Linux 的命令适配不等于所有功能都已完成该平台实机验收。抽象计算核心的图灵完备论证与工程边界见[语言契约](docs/LANGUAGE_CONTRACT.md)，不以复制 Python／Lisp 全部功能作为目标。

## 更新时保留自己的程序

取得新版完整 project ZIP 或 portable ZIP，停止本项目的 IDE 与程序，然后在原工程中执行：

```sh
./chaos replace "/absolute/path/Chaos-Project-New.zip"
./chaos status
```

更新先校验与试运行，再切换运行环境；保留项目源码、配置与数据。需要时用 `./chaos rollback` 恢复上一环境及配套依赖。若 `dependenciesReady` 为 `false`，按提示运行 `./chaos setup --yes`。

**不要把新版 ZIP 直接覆盖解压到旧工程。** [更新与回退指南 →](docs/UPDATES.md)

## 按你的目标继续学习

| 我想…… | 从这里开始 |
| --- | --- |
| 第一次运行，熟悉界面 | [快速开始](docs/GETTING_STARTED.md) · [用户指南](docs/USER_GUIDE.md) · [IDE](docs/IDE.md) |
| 配置模型，编写智能体 | [模型配置](docs/MODEL_CONFIGURATION.md) · [五个模型示例](docs/MODEL_EXAMPLES.md) |
| 学习这门语言 | [语言参考](docs/LANGUAGE.md) · [智能体函数](docs/AGENT_LANGUAGE.md) · [语言契约](docs/LANGUAGE_CONTRACT.md) |
| 生成报告、演示、网络页面 | [生成式 UI](docs/GENERATIVE_UI.md) · [实时网络](docs/LIVE_NETWORK_UI.md) |
| 让智能体基于执行经验改进 | [Harness 持续改进](docs/HARNESS_EVOLUTION.md) · [源码与状态演进](docs/PROCESS_EVOLUTION.md) |
| 开发语言、扩展宿主或发布 | [开发者指南](docs/DEVELOPER.md) · [SDK](docs/SDK.md) · [发布指南](docs/RELEASING.md) |
| 排查问题 | [环境配置](docs/DEPLOYMENT.md) · [故障排查](docs/TROUBLESHOOTING.md) |

完整专题见[文档中心](docs/README.md)。从 GitHub 源码开发时，先在自己的 Python 虚拟环境里执行 `python3 -m pip install .`，再用 `python3 -m chaos serve`；普通网页无需 npm 构建。[源码安装与测试 →](docs/DEVELOPER.md)

## 许可：源码可见，禁止用于 AI／大模型训练

原创源码、文档和示例采用 **Chaos No-AI-Training License 1.0**。允许学习、运行、修改、分发和商业使用；未经权利人另行书面许可，不得将这些材料用于模型预训练、微调、蒸馏、强化学习或训练数据集。正常模型推理、AI 编程辅助，以及用本语言处理自己的数据不因此被禁止。

完整约束以 [LICENSE](LICENSE) 为准；第三方组件继续适用各自原许可，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。这是一项带用途限制的源码可见许可，不是 OSI 定义的开源许可。许可声明不是技术防抓取措施，也不能撤销向托管平台另行授予的权利；范围与平台条款说明见[许可指南](docs/LICENSING.md)。


## 支持项目

如果 Chaos Intelligence 对你有帮助，欢迎自愿赞赏，支持持续开发、文档完善与维护。赞赏不影响使用权限，也不改变[项目许可](LICENSE)。

| 微信 | 支付宝 |
| --- | --- |
| <a href="docs/assets/support/wechat.jpg"><img src="docs/assets/support/wechat.jpg" alt="微信赞赏收款码" width="280"></a> | <a href="docs/assets/support/alipay.jpg"><img src="docs/assets/support/alipay.jpg" alt="支付宝赞赏收款码" width="280"></a> |

点击图片可查看原图，用对应应用扫码即可自愿赞赏。感谢每一位使用、反馈问题、贡献代码或支持项目的朋友。
