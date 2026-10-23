# Chaos 目录编程环境

将完整 project ZIP 解压到一个新目录，在这个目录打开终端。需要 Python 3.9+；macOS／Linux 使用 `python3`，Windows 使用 `py -3`。本包包含语言、标准库、网页和文档，不包含 Python、所有第三方依赖、模型密钥或 GPU 驱动。

## 先运行，再打开 IDE

```sh
python3 chaos.py run
```

默认执行 `main.chaos`，结果是 `42`。编辑自己的 `.chaos` 源文件，检查和运行都不需要模型密钥。第一次使用 IDE 时：

```sh
python3 chaos.py setup --yes
python3 chaos.py
```

setup 显式安装当前项目环境的依赖，需要可访问的 Python 包来源；不向全局 Python 安装。IDE 打印实际地址并打开浏览器。文档在该地址的 `/docs`，模型设置在 `/control`；按 `Ctrl+C` 结束前台服务，关闭浏览器不会结束服务。

## 配置一次，以后复用

`python3 chaos.py setup --models` 在真实终端询问共享地址、隐藏输入的密钥及各层模型；已有自定义设置保留。默认规划、执行、复核分别使用已配置提供方的 gpt-6-astra、gpt-6-sol、gpt-6.1-sol 模型 ID，实际供应商权限仍需验证。

- `python3 chaos.py setup --yes --profile core`：仅语言核心，无需安装第三方依赖。
- `python3 chaos.py doctor`：隔离自检当前环境，并检测可选 CUDA／MPS 后端，不修改模型配置。
- `python3 chaos.py setup --ai`：使用已配置规划模型提供一次部署建议，并记住启用偏好；`--no-ai` 关闭。
- `python3 chaos.py setup --yes --json`：stdout 输出单个 JSON，进度在 stderr；`--no-color` 或 `NO_COLOR` 关闭终端颜色。

标准部署检测系统、架构和 Python，临时安装依赖，完成递归／模块／异步自检后启用，并保存 `.chaos/deployment.json` 与私有 `.chaos/settings.json`。不会运行你的入口程序。换环境会重新验证；设备与机器人能力仍使用原授权规则。完整教程在在线文档“环境检测与配置”。0.153.0 起发行的启动器支持 `configure` 与 `setup` 共用流程；升级后仍保留更早官方启动器时，使用 `setup --yes` 或 `setup --models` 进入新流程。

## 放进已有工程

从这个新包目录执行，再进入目标工程：

```sh
python3 chaos.py install "/absolute/path/my-project"
cd "/absolute/path/my-project"
python3 chaos.py run
```

新工程只增加干净运行环境与三个文件：`chaos.py`、`chaos.project.json`、`main.chaos`，不复制分发目录里的私人源码、配置或数据库。文件冲突时保留原内容；已有 Chaos 项目的迁移见下方。目标已有的 `.gitignore` 不会被改写。

## 日常命令

| 命令 | 结果 |
| --- | --- |
| `python3 chaos.py check` | 检查默认入口的语法与模块结构 |
| `python3 chaos.py -c '40+2'` | 不读取入口文件，直接计算并返回 42 |
| `python3 chaos.py repl --empty` | 打开纯语言交互会话；`.help` 查看用法，`.quit` 退出 |
| `python3 chaos.py docs list` | 离线列出随包文档主题 ID |
| `python3 chaos.py docs show getting-started` | 输出本版本的快速开始 Markdown |
| `python3 chaos.py fmt` | 向终端输出排版结果，不覆盖源码 |
| `python3 chaos.py run --input-json 21` | 把数字 21 作为 input 运行默认入口 |
| `python3 chaos.py run --stream` | 持续输出 NDJSON 公开事件，成功时追加 `run.result` |
| `python3 chaos.py run --persistent` | 保存 CLI 历史到 `.chaos/cli.sqlite3` |
| `python3 chaos.py ide --no-browser` | 启动 IDE，只打印地址，不自动打开浏览器 |
| `python3 chaos.py status` | 查看实际版本、活动运行目录、依赖准备状态 |
| `python3 chaos.py --version` | 打印本项目使用的版本 |

`run`、`check`、`fmt` 和 `repl` 可在命令后指定项目内文件，例如 `python3 chaos.py run hello.chaos`。不指定文件时读取 `chaos.project.json` 的 entry；`repl --empty` 不预加载入口。IDE 的默认运行按钮也执行 entry，选中另一个文件不改变入口。启动器绑定它所在的工程；操作其他完整工程时使用那个工程自己的 `chaos.py`，不能用 `--directory` 切换。

## 在界面里体验实时交互

先保存当前程序。打开本项目服务地址的 `/docs`，找到“实时交互与界面／无界面开发”中的“从运行到追加指导”，将其中的 `live-progress.chaos` 或 `interactive-progress.chaos` 下载到自己的工程。示例无需模型密钥；按教程把代码用于入口并运行，在底部“运行结果”和“执行事件”观察公开进度。

向上阅读事件时不会强制跳到底部，点击“跟随最新”恢复跟随。互动示例等待指导时，从命令面板选择“运行：追加当前运行指导”，发送文本并核对接收回执和最终结果。普通程序必须主动调用指导接收函数才能使用新输入。

也可在终端运行下载的示例：

```sh
python3 chaos.py run live-progress.chaos --stream
```

普通 run 只打印最终 JSON；stream 输出每行一个 JSON 的公开事件。POSIX 真实终端还可用 `python3 chaos.py run interactive-progress.chaos --interactive`，在 `guide>` 提交文本；Windows 或重定向使用 stream，并通过网页、SDK或HTTP向相应服务运行追加指导。终端本地 run 与网页服务中的运行是不同实例。

需要模型编程助手时，先在 `/control` 配置服务，在 `/docs` 阅读“模型连接与智能体学习示例”与“大模型智能体：五个可运行示例”。打开 IDE 助手并发送需求后，查看“执行过程”、代码差异和校验结果，再显式“应用并保存”。读取失败时先等自动恢复，不重复提交原请求；关闭网页不会替代停止任务。

## 直接替换新版

先停止本项目的 IDE 和程序，在原工程中执行：

```sh
python3 chaos.py replace "/absolute/path/new-project.zip"
python3 chaos.py status
```

接受完整 project ZIP 或 portable ZIP。通过完整性校验和语言自检后才切换环境，不覆盖源码、设置或数据库。若显示 `dependenciesReady: false`，执行 `python3 chaos.py setup --yes` 再启动完整功能。需要回退时使用 `python3 chaos.py rollback`；回退只恢复运行环境及配套依赖，不撤销用户数据或外部操作。

旧版若没有 replace 命令，从解压后的新版包目录执行 `python3 chaos.py install "/absolute/path/old-project"`。迁移只替换可识别的官方旧启动器；已支持环境协议的启动器保持原样，不支持该协议的定制入口会拒绝覆盖。不要把新版 ZIP 覆盖解压进旧工程，也不要只覆盖其中部分 Python 文件。

## 哪些文件属于自己

自己的源码位于工程根目录或子目录。部署偏好在 `.chaos/deployment.json`，模型配置在 `.chaos/settings.json`，IDE 状态在 `.chaos/project.sqlite3`，CLI 持久历史在 `.chaos/cli.sqlite3`；CLI 默认使用内存历史。初始环境在 `.chaos/runtime` 与 `.chaos/deps`，换版后以 status 返回的 runtime 为准，不往运行环境里放业务文件。

网页与磁盘周期同步；双端冲突时保留双方并停止同步。移动工程要保留隐藏的 `.chaos` 及工作区同步文件。更换 Python／操作系统／架构后重新检查并准备依赖。给别人交付环境时使用干净发行包；备份自己的工程时先停止进程并保留源码、设置和状态。

## 命令行与内置智能帮助

先用 `python3 chaos.py docs list` 查主题，再用 `docs show TOPIC_ID` 离线读取对应教程。已配置模型后，可以直接提问：

```sh
python3 chaos.py ask '解释函数如何组合智能体' --role assistant --topic getting-started --stream
```

`ask` 和 REPL 的 `.ask 问题` 使用本项目设置，不自动发送源码或执行回答；相对 `--config` 从本项目根目录解析。自己的程序可导入 `std/intelligence`，用普通函数组合内置规划、执行、复核与助手，实际模型调用使用 `run`。

需要让助手修改项目时，保持 IDE 服务运行，在另一终端使用 `python3 chaos.py assist ask '需求' --server 实际服务地址 --project workspace --stream`。助手可以在私有草稿中进行受限验证；检查提案后用显式 apply 应用版本。它读取服务已保存的源码，不自动读取本地未同步的修改。完整命令、模型路由和提案恢复方法见 `python3 chaos.py docs show command-line`，也可在服务 `/docs` 选择“命令行编程与内置智能帮助”。

## 许可与 AI 训练限制

本项目原创材料采用 Chaos No-AI-Training License 1.0：允许运行、学习、修改、分发和商业使用，禁止未经另行书面许可将源码等受保护材料用于 AI／大模型训练、微调、蒸馏及训练数据集。正常模型调用和智能体工作仍可使用。第三方组件保留各自许可证，不受本项目新增限制替代。

在当前运行环境的 `*.dist-info/licenses/` 中查看完整 LICENSE 和 THIRD_PARTY_NOTICES.md；运行 `python3 chaos.py docs show licensing` 查看中文说明。此声明不能保证防止抓取，也不覆盖权利人向托管平台另行作出的授权。
