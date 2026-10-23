# Chaos Intelligence

Chaos 是以函数为基础、Agent 为一等可调用值的编程语言与开发环境。同一运行时组合函数、模块、异步任务、工具和长期工作；网页、CLI 与 SDK 使用同一语言。语言目标、可依赖能力及验收范围见[语言契约](docs/LANGUAGE_CONTRACT.md)。

## 许可：源码可见，禁止用于 AI／大模型训练

原创源码、文档和示例采用 **Chaos No-AI-Training License 1.0**。允许学习、运行、修改、分发和商业使用；未经权利人另行书面许可，不得将这些材料用于模型预训练、微调、蒸馏、强化学习或训练数据集。正常模型推理、AI 编程辅助，以及用本语言处理自己的数据不因此被禁止。

完整约束以 [LICENSE](LICENSE) 为准；第三方组件继续适用各自原许可，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。这是一项带用途限制的源码可见许可，不是 OSI 定义的开源许可。许可声明不是技术防抓取措施，也不能撤销向托管平台另行授予的权利；范围与平台条款说明见[许可指南](docs/LICENSING.md)。

## 命令行与内置智能帮助

目录包直接运行 `python3 chaos.py -c '40+2'`，用 `repl --empty` 开始交互编程，用 `docs list` 和 `docs show getting-started` 离线查文档。配置模型后，`ask '问题' --topic getting-started` 可以带着本版本文档提供帮助；REPL 中也可输入 `.ask 问题`。需要修改项目时，使用 IDE 助手或连接同一服务的 `assist`，检查提案后显式应用。

自己的程序通过 `std/intelligence` 组合规划、执行、复核与助手，沿用项目的模型配置和工具权限。安装后的全局 `chaos` 命令、项目选择和实际使用示例见[命令行教程](docs/COMMAND_LINE.md)。

## 一个目录开始编程

取得可信的完整 **project ZIP**，解压到新目录并在其中打开终端。需要 **Python 3.9+**；macOS／Linux 使用 `python3`，Windows 将命令中的 `python3` 换成 `py -3`。

```sh
python3 chaos.py run     # 默认 main.chaos 返回 42
python3 chaos.py setup --yes   # 检测、安装依赖、自检并保存默认配置
python3 chaos.py         # 打开当前项目 IDE
```

纯语言计算可以离线运行，无需模型密钥。setup 安装依赖到项目环境，需要可访问的 Python 包来源。IDE 会打印实际地址并打开浏览器：`/docs` 是文档，`/control` 是模型配置与后台任务。按 `Ctrl+C` 结束前台服务；关闭网页只结束页面观察。

首次配置会显示五个阶段、真实安装耗时和测试结果；交互终端自动着色。需要问答式配置模型时运行 `python3 chaos.py setup --models`，仅使用语言核心可选 `python3 chaos.py setup --yes --profile core`。`python3 chaos.py doctor` 复查当前环境；`--json` 输出机器可读报告，`--no-color` 关闭颜色。

配置按提供方、规划／执行／复核角色、单个智能体继承；已有选择保留在项目中。已配置模型后，可显式用 `setup --ai` 请求一次部署建议并记住此偏好，`setup --no-ai` 关闭。完整流程与旧启动器兼容说明见[环境检测与配置](docs/DEPLOYMENT.md)。

要在已有工程中使用，从刚解压的包目录执行：

```sh
python3 chaos.py install "/absolute/path/my-project"
cd "/absolute/path/my-project"
python3 chaos.py run
```

install 增加干净运行环境、`chaos.py`、`chaos.project.json` 和 `main.chaos`，遇到冲突保留原内容。它不复制分发目录里的私人源码、设置或运行历史。安装方式与旧项目迁移见[安装说明](docs/INSTALLATION.md)。

## 编辑、运行和查看进度

在 IDE 中选择 `main.chaos`，修改后点击“格式化”“保存”，再点击顶部“运行”。底部“运行结果”展示最终值，“执行事件”展示已发生的工作；向上阅读时暂停跟随，点击“跟随最新”回到底部。`Ctrl/Cmd-Shift-P` 搜索命令，`Ctrl/Cmd-P` 打开文件。

先按[快速开始](docs/GETTING_STARTED.md)完成 `double(21) → 42`，再按[从运行到追加指导](docs/REALTIME.md#从运行到追加指导)下载示例到自己的工程，练习持续进度和运行中输入。

| 要做什么 | 命令或入口 |
| --- | --- |
| 检查／运行已有文件 | `python3 chaos.py check hello.chaos` ／ `python3 chaos.py run hello.chaos` |
| 给程序输入数字 21 | `python3 chaos.py run double.chaos --input-json 21` |
| 终端持续接收公开事件 | 保存教程里的 `live-progress.chaos` 后，运行 `python3 chaos.py run live-progress.chaos --stream` |
| 在终端给当前运行追加指导 | POSIX 真实终端运行 `python3 chaos.py run interactive-progress.chaos --interactive`；详见实时教程 |
| 格式化并保存源码 | IDE“格式化”后“保存”；CLI `python3 chaos.py fmt hello.chaos` 只输出排版文本 |
| 保存 CLI 运行历史 | `python3 chaos.py run --persistent` |
| 查看实际版本与环境 | `python3 chaos.py --version` ／ `python3 chaos.py status` |
| 不开网页查用法 | `python3 chaos.py docs list` ／ `python3 chaos.py docs show command-line` |

普通 `run` 只输出最终 JSON；`--stream` 持续输出 NDJSON 事件，成功时追加同一运行的 `run.result`。`--interactive` 需要 POSIX 的输入和输出都是真实终端；Windows 或重定向场景使用 `--stream`，运行中指导通过网页、SDK 或 HTTP 发送给对应服务运行。事件包含程序公开进度、工具动作与结果。完整协议见[实时交互](docs/REALTIME.md)。

自己的源码放在工程根目录或子目录。默认入口由 `chaos.project.json` 的 entry 指定，选中文件只改变编辑对象。CLI 和 IDE 共用源码与设置，默认 CLI 内存历史、CLI 持久历史和 IDE 历史分别保存。磁盘同步、入口切换与草稿冲突见[用户指南](docs/USER_GUIDE.md)。

## 让智能体参与编程

在实际服务地址的 `/control` 按[模型配置教程](docs/MODEL_CONFIGURATION.md)保存自己有权使用的 Base URL、密钥和准确模型 ID。随后从活动栏打开编程助手，发送需求；对话会显示公开执行过程和代码提案。先核对差异与校验结果，再点击“应用并保存”。断线时保持原任务跟踪和当前草稿，恢复后补读事件，不需要重复提交同一个请求。

要在程序中调用大模型，从[五个模型智能体示例](docs/MODEL_EXAMPLES.md)开始：问答、多角色、并发、函数工具和 Bot。`local` 提供方是无需密钥的确定性演示；真实语义推理由 `model` 连接配置的服务完成。真实密钥留在私人配置中。

通过[Harness 持续改进](docs/HARNESS_EVOLUTION.md)，还可以把候选策略、真实执行比较和经验保留写进程序；同一组语言函数既能使用确定性操作，也能调用已配置的大模型。

## 直接替换新版本

停止本项目的 IDE 和程序，在原工程中执行：

```sh
python3 chaos.py replace "/absolute/path/Chaos-Project-New.zip"
python3 chaos.py status
```

replace 接受完整 project ZIP 或 portable ZIP，先校验并试运行，再切换完整环境；源码、配置、数据库和任务数据保留。如果 `dependenciesReady` 为 `false`，执行 `python3 chaos.py setup --yes` 再使用完整功能。`python3 chaos.py rollback` 恢复上一环境及配套依赖，不撤销用户数据或外部操作。

**不要把新版 ZIP 覆盖解压进旧工程。** 旧版没有 replace 命令时，从解压后的新版包执行 `python3 chaos.py install "/absolute/path/old-project"`。失败恢复与迁移细则见[更新说明](docs/UPDATES.md)。

## 用函数组织智能工作

```chaos
fn twice(f: Function, x) { f(f(x)) }
agent preview {
  prompt: "提供内容预览。",
  provider: "local",
  mode: "summarize",
  tools: []
};
{answer: twice(fn(x) { x + 1 }, 40), report: preview("Hello, Chaos")}
```

这段程序把函数作为参数，并像调用函数一样调用 Agent。`answer` 为42，`report` 是本地规则提供方的结构化预览；没有真实模型请求。类型注解在调用和返回处验证；外部工具需要宿主注册与显式授权。

持续机器人可以进一步组合 Agent／Team、Session、Process 和 Program，保存身份、邮箱、记忆和经测试的行为更新。设备控制使用同一效果与授权体系；软件验证和实际硬件验收范围见[机器人与设备](docs/ROBOT_DEVICES.md)及[树莓派 HID](docs/RASPBERRY_PI.md)。

## 按目标继续学习

| 目标 | 文档 |
| --- | --- |
| 第一次运行自己的项目 | [快速开始](docs/GETTING_STARTED.md) · [随包速查](docs/PROJECT_README.md) |
| 日常编辑、运行与恢复 | [用户指南](docs/USER_GUIDE.md) · [IDE](docs/IDE.md) · [故障排查](docs/TROUBLESHOOTING.md) |
| 查看实时进度并追加指导 | [实时交互教程与订阅接口](docs/REALTIME.md) |
| 使用大模型与智能体 | [模型配置](docs/MODEL_CONFIGURATION.md) → [模型示例](docs/MODEL_EXAMPLES.md) |
| 让智能体根据执行经验改进 | [Harness 持续改进](docs/HARNESS_EVOLUTION.md) · [源码与状态原子演进](docs/PROCESS_EVOLUTION.md) |
| 学语言及组合能力 | [语言参考](docs/LANGUAGE.md) · [Agent 函数](docs/AGENT_LANGUAGE.md) · [标准库](docs/STDLIB.md) |
| 嵌入其他工程或调用接口 | [开发者指南](docs/DEVELOPER.md) · [SDK](docs/SDK.md) · [HTTP API](docs/HTTP_API.md) |
| 安装、维护与发布 | [安装](docs/INSTALLATION.md) · [更新](docs/UPDATES.md) · [发布](docs/RELEASING.md) |

完整专题在[文档中心](docs/README.md)。包不包含 Python、模型密钥和 GPU 驱动；第三方依赖需要匹配目标系统及 Python。现有实机验收基于 macOS 的 Python 3.9 与 3.13，Windows／Linux 的命令适配不等于已经完成原生实机验收。

## 开发语言本身或嵌入宿主

GitHub 源码归档和可直接使用的 project ZIP 是两种交付物。源码根目录没有目录包的 `chaos.py`，从源码开始时先在自己的 Python 虚拟环境中安装，再使用 `chaos` 或 `python -m chaos`；普通网页无需 npm 构建。

```sh
python3 -m pip install .
python3 -m chaos serve
```

此独立服务默认地址是 `http://127.0.0.1:8765`，文档在 `/docs`；默认持久数据库是 `.chaos/workspace.sqlite3`，仅监听本机，面向单用户工作区。目录项目则使用其启动时打印的地址和项目内状态。

Python 宿主通过 `from chaos import Framework, Registry, Workflow, Client` 嵌入语言或调用服务。完整 portable 包可放入 vendor 目录，通过 `run_chaos.py` 使用原 CLI，或添加明确导入路径使用 SDK。公共接口、依赖和能力边界统一见[开发者指南](docs/DEVELOPER.md)与[SDK](docs/SDK.md)。

维护文档与发行包的检查命令：

```sh
python3 -m chaos docs check
python3 -m chaos docs build --output ./docs-site
python3 -m unittest discover -s tests -v
```

`docs check` 验证登记的确定性示例及本地链接；真实模型质量、设备和网络服务需要各自验收。文档发布流程见[文档维护](docs/MAINTAINING_DOCS.md)。

## 支持项目

如果 Chaos Intelligence 对你有帮助，欢迎自愿赞赏，支持持续开发、文档完善与维护。赞赏不影响使用权限，也不改变[项目许可](LICENSE)。

| 微信 | 支付宝 |
| --- | --- |
| <a href="docs/assets/support/wechat.jpg"><img src="docs/assets/support/wechat.jpg" alt="微信赞赏收款码" width="280"></a> | <a href="docs/assets/support/alipay.jpg"><img src="docs/assets/support/alipay.jpg" alt="支付宝赞赏收款码" width="280"></a> |

点击图片可查看原图，用对应应用扫码即可自愿赞赏。感谢每一位使用、反馈问题、贡献代码或支持项目的朋友。
