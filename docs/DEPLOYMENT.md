# 环境检测、交互配置与自检

解压完整项目包后，在包含 `chaos.py` 的目录执行 `./chaos setup`。系统检测当前操作系统、CPU 架构、Python 与活动运行环境，配置项目内依赖，实际运行语言自检，然后保存配置和检查记录。Windows PowerShell 将 `./chaos` 换成 `.\chaos.cmd`。项目包仍需要机器上已有 Python 3.9+，不包含操作系统安装器或 Python 本体。

若升级时保留的是 0.153.0 之前的旧官方启动器，请使用 `setup --yes` 或 `setup --models` 进入新运行时的部署流程；这类旧入口的无参数 `setup` 仍执行原安装逻辑，`configure` 会提示使用 `setup --yes`。0.153.0 起发行的启动器支持 `setup`／`configure` 共用流程。

已通过 pip 安装 SDK、仅用 `project init` 创建的工程共享宿主依赖；此流程检查它们，安装升级仍用宿主包管理器，`--reinstall` 仅用于完整目录包。全局命令可从工程外指定目录：

```sh
chaos project setup --directory /absolute/path/my-project --yes --profile core --no-ai
chaos project configure --directory /absolute/path/my-project --models
chaos project doctor --directory /absolute/path/my-project --json
```

这些命令使用当前安装的 Chaos 运行时。完整目录包要使用其自身选中的版本，应运行 `python3 /absolute/path/my-project/chaos.py setup`；绑定启动器不能通过 `--directory` 切换其他工程。尚未创建项目时先执行 `chaos project init DIR`。只想了解选项可在任何目录运行 `chaos project setup --help`，不会创建状态或安装依赖。

## 第一次使用

```sh
./chaos setup
./chaos run
./chaos
```

`setup` 首次默认使用 `standard` 层，之后沿用保存的层级；显式 `--profile` 可以更改。标准层准备模型 SDK、加密、版本处理与文档渲染依赖；它不修改全局 Python。终端依次显示环境、依赖、运行时测试、模型设置、保存结果五个阶段。每个阶段显示实际动作和耗时；通过／失败使用不同颜色，不把未知下载时间写成虚构的百分比。安装失败不会将未验证的临时依赖切换成活动依赖。

只想离线学习语言核心，可以选择并记住 `core` 层：

```sh
./chaos setup --profile core
./chaos run
```

核心层运行递归、模块和异步任务自检，不安装第三方依赖。可以用 `./chaos docs list` 和 `./chaos docs show getting-started` 离线查阅随包 Markdown。需要网页文档渲染或模型时，再执行 `./chaos setup --profile standard`。层级表示本项目要准备的运行依赖，不会自动授予 Agent 电脑控制、文件读写或设备权限。

## 交互填写模型连接

```sh
./chaos configure --models
```

在交互式终端中，向导询问统一 Base URL，以及各提供方的独立地址、Key、默认模型和角色模型。Key 隐藏输入，任何字段直接回车都保留已有设置；独立 Base URL 优先于这次输入的统一地址。`configure` 也会检查依赖、按所选层准备缺失依赖并运行自检。已经准备好依赖、只允许检查时加 `--skip-install`。

向导复用系统的同一份 `.chaos/settings.json`，不是另一套机器人配置。原有自定义提供方、角色、智能体、插件和权限设置保留。默认分层如下：

| 配置层 | 默认分类与行为 |
| --- | --- |
| 提供方 | `text`、`image1k`、`image4k`、`grok` 分别保存连接与凭据；全新配置的通用初始地址是 `https://api.openai.com/v1`；请按各服务实际地址和可用模型统一或逐项配置，不代表所有槽位均由该服务支持 |
| 角色 | `planner` 规划、`executor` 执行、`reviewer` 复核、`image` 出图、`grok`；角色可以独立选择模型 |
| 智能体 | `agents` 中按智能体 ID 覆盖角色、提供方、模型或连接；向导保留这些更具体的覆盖 |
| 能力与工具 | 继续由现有项目授权与插件设置控制；保存模型连接不会增加工具权限 |

默认规划模型为 `gpt-6-astra`，执行为 `gpt-6-sol`，复核为 `gpt-6.1-sol`；这是可编辑的路由配置，不证明当前账号支持这些型号。角色模型留空会保留原来的自动升级策略；显式输入新角色模型会固定该模型（`autoUpgrade=false`）。提供方、角色和智能体的继承详情见 [Harness](HARNESS.md)；图像角色选择 4K 等高级配置见[模型连接](MODEL_CONFIGURATION.md)。

配置采用版本比较和原子写入。输入取消、配置损坏或并发版本冲突不会用向导的旧值覆盖现有设置；先修复或重新加载后再运行。不要把 `.chaos/settings.json` 加入公共源码或分发包。

## 无人值守与环境变量

```sh
./chaos setup --yes --no-ai
./chaos configure --yes --models --skip-install
./chaos doctor --json
```

`--yes` 关闭询问；未提供的新值保持原设置。只有显式指定 `--models` 时，向导才把下面的环境变量导入持久配置。无 Key 可以正常完成核心部署，模型建议会说明尚未配置。

| 变量 | 用途 |
| --- | --- |
| `CHAOS_BASE_URL` | 统一提供方地址 |
| `CHAOS_TEXT_KEY`、`CHAOS_IMAGE_1K_KEY`、`CHAOS_IMAGE_4K_KEY`、`CHAOS_GROK_KEY` | 各分类凭据 |
| 上述前缀加 `_BASE_URL` 或 `_MODEL` | 提供方单独地址或默认模型；例如 `CHAOS_TEXT_BASE_URL` |
| `CHAOS_PLANNER_MODEL`、`CHAOS_EXECUTOR_MODEL`、`CHAOS_REVIEWER_MODEL` | 固定规划、执行、复核角色模型 |
| `CHAOS_IMAGE_MODEL`、`CHAOS_GROK_ROLE_MODEL` | 固定图像和 Grok 角色模型 |

变量使用自己的值，具体平台写法见[模型连接示例](MODEL_CONFIGURATION.md#用脚本保存到当前项目)。这些变量仅是配置入口；导入后运行时读取持久设置。为避免临时配置与持久配置混用，存在非空 `CHAOS_MODEL_URL/KEY/NAME` 时向导会要求先移除它们。`OPENAI_API_KEY` 不会被自动导入。

`--json` 把一个完整报告写入标准输出，进度写入标准错误，并关闭交互提示。`--no-color` 或 `NO_COLOR` 环境变量禁用颜色；输出重定向或非交互终端也不输出 ANSI 配色。报告不包含凭据或 Key 尾号。

## 可选智能建议

准备好规划角色的连接之后，显式开启一次建议并记住选择：

```sh
./chaos configure --ai
```

这会调用 `intelligence-deployment` 智能体路由，默认继承 `planner`，也可在同一份 Settings 的 `agents` 中单独覆盖。它复用语言智能体的原生 Harness、插件设置和候选模型切换，**可能产生推理费用**。建议最多一个智能体步骤、总执行期限不超过 20 秒，每次模型请求最多 384 tokens；失败切换可能产生多次请求，384 不是整个建议任务的总用量承诺。

仅发送系统类型、架构、Python 版本等有限环境摘要，不发送源码、项目路径、凭据或用户文件。没有工具声明或执行权限；模型返回的建议在终端展示，不会被当作命令执行，也不会自动改变配置或权限。该路由需要 `native` 后端；选择其他后端会明确跳过，禁用 native 插件会报告建议不可用，均不会隐式启用插件或改用其他后端。

选择写入项目的 `.chaos/deployment.json`，以后的 `setup`／`configure` 默认沿用，可能再次发起模型请求；普通 `run`、启动 IDE 和 `doctor` 不因这个部署偏好调用建议模型。关闭并记住选择：

```sh
./chaos configure --no-ai
```

没有凭据、SDK 不可用或模型请求失败时，报告跳过／失败原因，已经通过的离线检查仍有效。模型建议不代替实际运行测试，也不自动检测账号模型目录。希望更新提供方模型目录时，按[模型连接](MODEL_CONFIGURATION.md)使用单独的检测操作。

## 迁移系统与验收

```sh
./chaos doctor
./chaos setup --yes
```

`doctor` 不安装、不询问、不保存模型设置、不调用 AI，只检查当前活动环境。自检在独立临时进程中执行固定的递归函数、跨文件模块和异步任务，不运行用户的 `main.chaos`；标准层还检查实际依赖可导入。GPU 检查报告当前运行环境是否能使用已安装的 PyTorch CUDA／MPS 后端，未安装表示尚未配置，不表示机器没有 GPU。GPU 驱动、PyTorch、浏览器、ADB 和机器人硬件需要另行准备，见 [GPU](GPU_COMPUTE.md)、[机器人与设备](ROBOT_DEVICES.md)。

更换系统、架构、Python 或运行时依赖需求后，指纹变化会显示提示；运行 `setup` 为新环境准备匹配依赖。保留在项目根下的 `.chaos/settings.json` 不会因为换环境被重置。`.chaos/deployment.json` 保存环境指纹、部署层、AI 建议偏好和最近一次成功自检；它不是对所有设备或所有业务程序的验收证书。

如果依赖指纹仍匹配，但实际文件被删除或损坏，`doctor` 会通过真实导入发现问题。标准层可以强制重新准备依赖：

```sh
./chaos setup --profile standard --reinstall
```

`--reinstall` 在临时目录重新安装并执行自检；候选全部通过后才切换，旧依赖在此之前保留，安装或测试失败不会启用半成品。该选项需要包来源可用，不能与 `--skip-install` 合用；`doctor` 和 `core` 层拒绝此选项。它只修复环境依赖，不重置自己的源码、模型设置和历史数据。

执行失败返回非零退出码，并显示失败阶段。配置文件损坏时先恢复自己的备份，不要删除设置后重试来掩盖问题。完整安装方式见[安装与部署](INSTALLATION.md)，版本替换见[更新](UPDATES.md)，日常操作见[用户指南](USER_GUIDE.md)。
