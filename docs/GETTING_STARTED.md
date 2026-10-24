# 快速开始

这条路径从一个完整项目目录开始，执行程序再打开同目录 IDE。需要 Python 3.9+；纯语言核心无需网络、模型密钥、浏览器组件或 Codex App。

日常命令以语言名 `chaos` 为入口：已安装 CLI 用 `chaos`，本教程的免安装 project ZIP 用 `./chaos`（Windows PowerShell 用 `.\chaos.cmd`）。源码仓库用 `./bin/chaos -c '40 + 2'`。当前运行时需要 Python 3.9+，入口自动寻找解释器；旧目录包或需显式选择解释器时，可将本教程的 `./chaos` 换为 `python3 chaos.py`。源码 CLI 与目录项目的配置和启动方式见[安装说明](INSTALLATION.md#从源码安装)。

## 1. 解压或安装到自己的工程

将可信发布者提供的完整 **project ZIP** 解压到新目录。确认根目录有 `chaos.py` 和 `main.chaos`，然后执行默认入口，模板结果为 `42`：

```sh
cd "/absolute/path/my-project"
./chaos run
```

macOS／Linux 使用 `./chaos`，Windows PowerShell 将文中 `./chaos` 换为 `.\chaos.cmd`。包不包含 Python，需要先有 Python 3.9+。

如果已经有自己的工程，在刚解压的分发目录执行：

```sh
./chaos install "/absolute/path/existing-project"
cd "/absolute/path/existing-project"
./chaos run
```

已有其他工程文件不影响安装；若 `chaos`、`chaos.cmd`、`chaos.py`、`chaos.project.json`、`main.chaos` 或要创建的环境位置发生冲突，安装会拒绝，不覆盖内容。install 安装干净运行时和五个初始文件（含 `chaos`、`chaos.cmd` 命令入口），不复制分发目录里的用户设置、密钥、源码或运行历史。支持 replace 的 Chaos 工程直接用 replace 更新。更早的旧项目可从新版包执行 install 到旧工程；只迁移与原包一致的官方启动器，已支持环境协议的启动器保持原样；不支持该协议的定制旧入口会拒绝覆盖。

自己的 `.chaos` 模块放在工程根目录或子目录，避免放进管理环境的隐藏 `.chaos` 目录。初始运行时位于 `.chaos/runtime`；更新后的活动环境由启动器选择。配置和状态仍保存在本工程内。源码开发或 wheel 安装方式见[安装说明](INSTALLATION.md)。

## 2. 执行第一个程序

创建 UTF-8 文件 `hello.chaos`：

```chaos example=getting-started-hello
fn square(x: Int) -> Int { x * x }
{message: "Hello, Chaos", answer: square(7)}
```

```sh
./chaos check hello.chaos
./chaos run hello.chaos
```

`check` 返回程序检查摘要；`run` 返回 JSON：

```json
{"message": "Hello, Chaos", "answer": 49}
```

Chaos 的最后一个表达式就是结果。`let` 声明不可变绑定，`fn` 定义函数，记录用 `{key: value}` 表达。类型注解是运行时契约。完整语法见 [语言参考](LANGUAGE.md)。

创建 `double.chaos` 接收 JSON 输入：

```chaos example=getting-started-input
fn double(x: Int) -> Int { x * 2 }
double(input)
```

```sh
./chaos run double.chaos --input-json 21
```

结果为 `42`。上述命令仅本次运行 `double.chaos`，不修改默认入口。通常保留 `main.chaos` 并导入更多模块即可；已有 IDE 状态时换入口需同时协调项目标记与已保存项目，按[用户指南](USER_GUIDE.md)操作。只在文件列表中选中它不会切换入口。

`input` 默认是 `null`；字符串输入要写为 JSON 字符串，例如 `--input-json '"hello"'`。省略引号的 `hello` 不是合法 JSON。

## 3. 区分运行、检查、格式化与 IR

```sh
./chaos fmt hello.chaos
./chaos check hello.chaos
./chaos run hello.chaos --trace
./chaos repl --empty
```

| 命令 | 行为 |
|---|---|
| `check` | 解析语法，检查模块依赖和导出；不执行程序 |
| `run` | 解释执行，最终结果写到 stdout；出错返回非零状态 |
| `fmt` | 将入口文件格式化后写到 stdout，不覆盖文件；在 IDE 中格式化后保存可写回 |
| 原 CLI 的 `ir` | 已安装 `chaos` 时，以 `chaos ir hello.chaos` 导出 JSON 程序结构；不是本轮目录启动器命令 |
| `repl --empty` | 不预加载入口的交互求值；`.env` 查看绑定，`.reset` 重置，`.quit` 退出 |

目录入口的 `repl` 若省略 `--empty`，会先求值项目入口；模型或设备程序应使用 `run`。在 REPL 中输入 `.help` 可看离线提示，配置模型后输入 `.ask 问题` 获得帮助；它不自动发送当前变量或源码，也不执行模型回复。

无需启动网页也可查文档：`./chaos docs list` 列出主题，`./chaos docs show getting-started` 输出本页 Markdown；两者不调用模型。

解析和 IR 生成不等于生成机器码。原 `chaos compile` 提供同源编译分析报告；可选原生编译后端的范围见[编译指南](COMPILER.md)。构建 Python wheel 是打包运行环境，详见 [开发者指南](DEVELOPER.md)。

## 4. 打开网页

首次准备完整 IDE／文档依赖后，无参数启动当前项目：

```sh
./chaos setup --yes
./chaos
```

setup 显式从配置的包来源安装依赖，先写入项目内临时目录，成功后为当前运行环境启用；依赖不会安装到全局 Python；解释器缺少 pip 时，会在临时目录引导安装工具。普通编程不会自动联网安装，以后通常只需运行 `./chaos`。IDE 默认选择本机空闲端口、打印实际地址并打开浏览器，进入当前目录的源码工作台；不固定使用 8765。在左侧选择文件，中间编辑源码，保存后运行入口程序，在底部输出中查看结果、事件和历史。首次打开时编程助手关闭；需要时从活动栏打开。默认项目计算无需模型密钥。

部署会显示系统检测、依赖安装、语言自检、分层模型设置和持久化五个阶段。需要问答式配置模型时执行 `./chaos setup --models`；仅离线语言可选择 `--profile core`。之后用 `./chaos doctor` 复查环境。详见[环境检测与配置](DEPLOYMENT.md)。

按 `Ctrl/Cmd-Shift-P` 搜索所有功能，按 `Ctrl/Cmd-P` 快速打开项目文件。运行输入、JSON解析和运行选项从运行设置打开；关系画布、程序结构、Team和Program等高级功能仍可从命令面板进入。网页只是同一个语言项目的编辑视图，不需要同时展开所有面板。快捷键、草稿保护和小屏操作见[IDE指南](IDE.md)。

项目启动器将设置与 IDE 的持久数据库 `.chaos/project.sqlite3` 放在项目内。无界面 run 默认使用独立内存数据库；需要保留CLI记录时显式加 `--persistent`，使用 `.chaos/cli.sqlite3`，仍不与 IDE 共享运行历史；两者仍读取同目录源码和设置。使用 `Ctrl+C` 结束前台服务，再从同一目录启动可继续工作。在线文档位于服务地址的 `/docs`，模型配置与后台任务位于 `/control`。

网页和磁盘文件通过原 Workspace 协议双向周期同步，存在短暂延迟；外部编辑器必须先保存文件。双端冲突时保留双方并报告，停止自动同步，避免旧版本覆盖新代码。根据冲突报告合并后再继续。运行和保存仍使用原项目版本，详细边界见[实时同步](REALTIME.md)。

把整个项目移到新目录后，仍从该目录的 `chaos.py` 启动；无需修改导入路径。已安装的依赖应与目标 Python／平台兼容。单独运行已安装的 `chaos run file.chaos` 仍是原独立入口，不应把它误认为目录项目的实时同步。

完整的目录命令速查、入口文件与数据位置见[用户指南](USER_GUIDE.md)。

## 5. 看见运行过程，并追加一次指导

先完成上一节的保存与运行，再按[从运行到追加指导](REALTIME.md#从运行到追加指导)将 `live-progress.chaos` 和 `interactive-progress.chaos` 下载到自己的工程。这两个示例无需模型密钥；教程会说明如何保留原程序、在哪个入口运行以及核对哪些结果。

- **看进度**：运行进度示例，在底部“执行事件”观察持续追加的公开记录，在“运行结果”观察阶段数据，最终结果为 `{results: [1, 4, 9], total: 3}`。向上阅读时暂停跟随，点击“跟随最新”回到底部。
- **发指导**：互动示例会在明确的接收点等待。打开命令面板，选择“运行：追加当前运行指导”，输入文本并发送；核对已入队、已接收与最终输出。示例最多等待30秒，超时路径见教程。随意向不接收指导的程序发送文本不会改变它的逻辑。
- **用终端看同样的公开记录**：在工程中执行下面命令，连续输出 NDJSON 事件；成功时最后追加 `run.result`。默认不加 stream 的 run 仍只输出最终 JSON。

```sh
./chaos run live-progress.chaos --stream
```

macOS／Linux 的真实终端还可以运行 `./chaos run interactive-progress.chaos --interactive`，在 `guide>` 输入文本。interactive 需要 POSIX 的 stdin/stdout 都是真实终端；Windows 或重定向使用 stream，并通过网页／SDK／HTTP给相应服务运行发送指导。本地命令启动的 Run 与网页服务中的 Run 不是同一个实例，不能交叉使用它们的 ID。

关闭网页只断开观察，服务端运行继续；本地 interactive 的 `/cancel`、Ctrl-C 或 Ctrl-D 会请求取消并等待清理。查看进度、接收指导和程序完成是不同状态，详细协议见[实时交互](REALTIME.md)。

## 6. 配置模型并使用编程助手

打开实际服务地址的 `/control`，按[模型配置](MODEL_CONFIGURATION.md)保存自己的 Base URL、密钥和准确模型 ID。打开 IDE 编程助手，发送一个具体需求，例如“为当前函数增加边界检查，并解释代码变更”。打开面板本身不会发送模型请求。

对话里的“执行过程”显示公开阶段；完成后检查代码差异与校验结果，再点击“应用并保存”。若期间继续编辑源码或出现版本冲突，先保留草稿，按[IDE 的提案流程](IDE.md)重新核对，不强制覆盖。断线时原任务与取消入口保留，等待恢复即可；无需重复发送同一请求。

不需要打开网页时，也可先用终端保存模型配置，再带着本版本文档提问：

```sh
./chaos configure --models
./chaos ask '给出一个函数组合的短例子，并解释它' --role assistant --topic getting-started --stream
```

`ask` 只返回回答，不修改源码。需要在终端操作同一个项目编程助手时使用 `assist`，明确指定实际服务地址与项目 ID，见[命令行教程](COMMAND_LINE.md)。

要在自己的程序中调用智能帮助，可导入 `std/intelligence` 并组合其 `planner`、`executor`、`reviewer`、`assistant`；继续学习[五个模型智能体示例](MODEL_EXAMPLES.md)。模型调用可能产生供应商费用，配置成功与某个任务回答正确是两项不同验证。

## 7. 直接替换新版本

取得可信的新 project ZIP 或 portable ZIP，退出当前项目的 IDE／后台程序，然后在原项目目录执行：

```sh
./chaos replace "/absolute/path/Chaos-Project-New.zip"
./chaos status
./chaos --version
./chaos run
```

替换会校验完整包并执行候选语言自检，成功后切换运行环境；源码、模型设置和数据库留在原位。依赖需求与 Python／平台指纹匹配时可继承，否则按提示执行 `./chaos setup --yes`。此流程使用本地取得的包，不要求配置在线更新地址。

需要恢复上一环境时，关闭本项目的 IDE／后台程序后执行 `./chaos rollback`，再照常启动。回退包括该环境的依赖，不撤销源码编辑、配置修改或程序产生的数据。不要将新 project ZIP 覆盖解压到原工程，也不要把用户文件放进版本环境中。验证、保留与回退范围见[更新说明](UPDATES.md)。

## 8. 继续学习

- 要编写更多程序，阅读 [语言参考](LANGUAGE.md)、[标准库](STDLIB.md) 和 [高级编程](ADVANCED.md)。
- 要保存、运行、导入和同步项目，阅读 [用户指南](USER_GUIDE.md)。
- 要组合真实模型与函数工具，阅读[模型智能体示例](MODEL_EXAMPLES.md)，再按[控制平面](HARNESS.md)理解工具与后台任务边界。
- 要把 Chaos 嵌入其他应用，阅读 [开发者指南](DEVELOPER.md) 和 [SDK](SDK.md)。
- 要让服务长期运行、保留数据和接收更新，阅读 [安装与部署](INSTALLATION.md)。
