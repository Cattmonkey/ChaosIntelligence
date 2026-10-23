# 命令行编程与内置智能帮助

Chaos 可以直接在终端编程，也可以从语言程序调用内置智能体。命令行、网页和 SDK 使用同一语言运行时、模型分层和权限规则；新增入口只是现有能力的入口，不是另一套解释器或模型服务。

## 使用 chaos 命令

安装发行包中的 wheel 后，Python 环境会提供 `chaos` 命令。推荐安装到自己的虚拟环境，避免修改全局 Python：

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install /absolute/path/chaos_intelligence-X.Y.Z-py3-none-any.whl
chaos --version
chaos --help
```

`X.Y.Z` 替换为实际取得的发行版本；示例路径同样要替换。Windows 创建环境用 `py -3 -m venv .venv`，PowerShell 激活用 `.venv\Scripts\Activate.ps1`；也可直接运行 `.venv\Scripts\chaos.exe`。无法使用激活脚本时，POSIX 可运行 `.venv/bin/chaos`。安装基础依赖需要可访问的 Python 包来源。源码开发者可以在源码根目录使用 `python -m pip install .`。

完整目录包无需安装 PATH 命令：文件运行、`-c`、`repl`、`ask`、`assist` 和 `docs` 可使用 `python3 chaos.py` 入口。全局的 `ir`、`compile`、`service` 等其他命令应使用已安装 CLI 或 portable SDK 的 `python3 /absolute/path/run_chaos.py`，不能把所有命令都机械替换成目录入口。安装好的模块还可以通过 `python -m chaos` 调用全局 CLI。不要在含有同名 `chaos.py` 的项目根目录使用模块入口，以免遮蔽包；此时使用目录启动器。

`chaos project --help` 列出项目命令，`chaos project setup --help`、`chaos project ask --help` 等帮助可在工程外查看，不创建项目、不安装依赖或调用模型。完整目录包也可用 `python3 chaos.py --help` 查看入口。

## 先选项目，再选择配置

完整目录包的 `chaos.py` 绑定其所在工程与该工程的活动运行时。从任何工作目录使用它的绝对路径即可操作同一工程。已安装 CLI 可以显式选择项目：

```sh
chaos project doctor --directory /absolute/path/my-project --profile core
chaos project configure --directory /absolute/path/my-project --models
chaos project ask '解释模块导入的写法' --directory /absolute/path/my-project --topic getting-started
```

没有 `--directory` 时，项目命令从当前目录向上查找 `chaos.project.json`。全局 `chaos project` 使用当前安装的运行时；若要确保使用完整目录包自己选择的版本，运行目标工程自己的 `chaos.py`。绑定的启动器拒绝用 `--directory` 切换到其他工程，避免混用一个工程的运行时和另一个工程的数据。

项目 `run`、`repl`、`ask` 默认读取项目 `.chaos/settings.json`，可用 `--config` 明确覆盖；相对配置路径相对于选中的项目根目录解释。独立全局命令则依次使用显式 `--config`、`CHAOS_CONFIG`、用户默认配置，相对路径按调用时的工作目录解释。`setup`／`configure` 始终保存选中项目的设置。`assist` 的模型与设置来自 `--server` 指向的服务。

选择设置文件不等于关闭兼容环境变量。非空 `CHAOS_MODEL_URL` 激活 text 提供方及文本角色的临时连接／模型覆盖，配套值来自 `CHAOS_MODEL_KEY`／`CHAOS_MODEL_NAME`。Settings 在每次解析路由时统一应用，普通 Agent、函数工具 Agent、Session 和 IDE 助手沿用同一规则；更具体的智能体覆盖仍优先。临时值不写入配置文件，移除变量后恢复持久配置。配置页显示保存值；若要完全使用保存值，先移除这三个变量。详见[模型配置](MODEL_CONFIGURATION.md#用脚本保存到当前项目)。

## 文件、表达式与管道

```sh
chaos -c 'fn twice(x) { x * 2 }; twice(21)'
chaos main.chaos
chaos run main.chaos --input-json 21
chaos check main.chaos
chaos fmt main.chaos
printf '40 + 2\n' | chaos run -
```

计算结果为 JSON，错误写 stderr 并返回非零状态。`fmt` 只输出排版文本；不直接覆盖源码。文件简写与 `run FILE` 等价，`chaos -` 与 `run -` 等价。`-c` 与 stdin 是独立源输入，只能导入 `std/*` 标准模块；需要本地相对导入时保存为文件并用 `--root` 指定程序根目录。输入长度、模块路径与执行预算沿用语言原有限制。

在目录项目中，`python3 chaos.py run` 执行项目入口；文件简写、`run -` 和 `-c` 不会先执行默认入口。目录路径边界与项目设置仍生效。裸 `python3 chaos.py` 继续打开 IDE；全局裸 `chaos` 保留启动服务的旧行为，交互编程请显式运行 `chaos repl`。

## 交互编程时随时获得帮助

```sh
chaos repl
```

输入 `let x=40`，再输入 `x+2`，即可得到 `42`。`.env` 查看当前绑定，`.reset` 清空会话，`.quit` 退出。多行输入由实际语言解析器判断是否完整。

```text
chaos> .help
chaos> .ask 怎样定义并调用一个智能体函数？
```

`.help` 显示离线使用提示；`.ask 问题` 显式使用已有模型配置并附上本版本“快速开始”文档，复用下面的 `ask` 运行链。它不会自动发送 REPL 的变量或源码。`chaos repl --config /path/settings.json` 可选择配置文件。目录入口 `python3 chaos.py repl` 保留预加载项目入口的旧行为；不希望执行入口时用 `python3 chaos.py repl --empty`，也可指定待预加载文件。普通 REPL 求值仍是纯 Session；真实模型、Bot 与设备程序使用 `run`，`.ask` 的结果不会成为自动执行的源码。

## 直接向内置智能体提问

不确定主题 ID 时，先离线查看本版本文档：

```sh
python3 chaos.py docs list
python3 chaos.py docs show function-tools
```

`list` 返回主题目录 JSON，`show` 将指定主题的 Markdown 原文写到 stdout，可重定向到自己的文件。它们读取当前运行时随包文档，不需要网页、模型 Key 或网络；未知主题会报错。已安装 CLI 对应 `chaos docs list`／`chaos docs show function-tools`。

需要解释、规划或示例时，再按[环境配置](DEPLOYMENT.md)或[模型配置](MODEL_CONFIGURATION.md)保存连接，显式调用智能体：

```sh
python3 chaos.py setup --models
python3 chaos.py ask '帮我规划一个数据分析流程' --role planner --stream
python3 chaos.py ask '这个语言如何使用函数工具？' --role assistant --topic function-tools
```

全局形式为 `chaos ask '问题' --config /path/settings.json`，不会自动发现并读取当前目录项目的配置；要使用项目默认配置，运行它的 `chaos.py ask` 或 `chaos project ask --directory DIR`。角色可选 `planner`、`executor`、`reviewer`、`assistant`；默认 executor。`--topic` 从安装版文档读取指定主题作为明确上下文；未知主题报错，不通过关键词猜测主题。`ask -` 从 stdin 读取问题文本。

此命令执行固定的 Chaos 程序，用户问题作为 JSON 数据传入 `std/intelligence.ask`；不会把问题拼接成代码。模型连接、失败切换与成本由原 Settings／Harness 处理。调用可能产生费用；无密钥会明确失败，不伪造智能回复。

安装时的 `setup`／`configure --ai` 同样使用 Settings、native 插件和 Harness，路由是默认继承 planner 的 `intelligence-deployment`。它仅处理受限环境摘要，返回建议，不执行工具；预算和持久偏好见[环境部署](DEPLOYMENT.md#可选智能建议)。日常问答、部署建议和 IDE 草稿编辑使用各自需要的上下文，共用模型连接与执行协议。

`--stream` 返回真实运行事件与同一运行的最终结果，复用 `run --stream`。事件不是保证逐 token 的文本流，也不展示隐藏推理。使用普通函数或 Bot 编写持续工作与指导边界，见[实时交互](REALTIME.md)。

## 在自己的程序里组合智能能力

```chaos
import "std/intelligence" as AI;
let plan = AI.planner(input);
AI.reviewer({goal: input, plan: plan})
```

将代码保存为文件，用普通 `run` 执行。导入与创建 Agent 都是本地操作，调用时才请求模型。四个默认值都是现有的 Agent 可调用值，可以作为函数参数、绑定语言函数工具，或交给 Session／Bot 等现有能力。

| 导出 | 角色与用途 |
| --- | --- |
| `planner` | planner 角色，规划；route 为 `intelligence-planner` |
| `executor` | executor 角色，执行；route 为 `intelligence-executor` |
| `reviewer` | reviewer 角色，复核；route 为 `intelligence-reviewer` |
| `assistant` | executor 角色，使用与编程帮助；route 为 `intelligence-assistant` |
| `ask(input, role="executor")` | 按显式角色调用上述值 |
| `make(prompt, role="executor", route="intelligence", tools=[], max_steps=8, name="intelligence_assistant")` | 通过原 `agent_from` 构造可配置的 Agent |

可直接下载学习源码：[只检查 Agent 值](../examples/intelligence/inspect.chaos)、[内置问答](../examples/intelligence/ask.chaos)、[函数组合](../examples/intelligence/compose.chaos)、[绑定计算工具](../examples/intelligence/function_tool.chaos)。后三个需要真实模型配置；检查值的示例不请求模型。

```chaos example=intelligence-values
import "std/intelligence" as AI;
[type_of(AI.planner), agent_info(AI.assistant).config.route]
```

提供方固定使用系统原有 `model`，模型和凭据从 Settings 解析；对某个内置智能体的独立覆盖填写对应 route。`make` 的 name 是语言身份，route 才是配置路由。默认不附带宿主工具，设置 tools 不会自动增加项目权限。用 `agent_bind` 绑定自己定义的 `agent_tool`，继续遵循[函数工具](FUNCTION_TOOLS.md)的类型与执行证据约束。

这些智能体可用于语言项目的 CLI／网页运行与 SDK 的 `Framework.start` 托管运行。`Framework.evaluate` 与普通 REPL 是纯求值入口，不能仅凭导入模块执行真实模型或设备操作；导入并查看 Agent 值仍可离线完成。更复杂的规划／执行／验证循环由函数、异步、Bot、Process 组合，见[模型示例](MODEL_EXAMPLES.md)。

## 终端与 IDE 使用同一个编程助手

需要读取项目、查询语言参考、修改草稿并验证时，先启动项目 IDE 服务，然后在另一个终端使用 `assist`。`--server` 填服务打印的实际地址，`--project` 填服务中的项目 ID，目录 IDE 通常是 `workspace`；下列地址仅为示例。完整目录包使用 `python3 chaos.py assist ...`。

`assist` 读取服务中的项目版本，不自动上传本地文件或网页尚未保存的草稿。需要让它处理最新代码，先保存并确认磁盘／网页同步。即使用 `chaos project assist --directory DIR ...`，本地目录也不会代替 `--server` 和 `--project` 选择远端身份。

```sh
chaos assist ask '检查这个项目，并提出可验证的改进' --server http://127.0.0.1:8891 --project workspace --stream
chaos assist history --server http://127.0.0.1:8891 --project workspace
chaos assist show JOB_ID --server http://127.0.0.1:8891 --project workspace
chaos assist events JOB_ID --after 0 --server http://127.0.0.1:8891 --project workspace
```

`assist ask` 默认返回已提交任务；`--stream` 跟随公开工具事件与最终提案。同一 session 可通过 `--session SESSION_ID` 继续对话。它调用网页 IDE 的同一服务、模型路由、文档工具和提案数据库，网页能看到该任务；本地 `ask` 则是独立语言运行，两者的任务 ID 不互换。

检查 `show` 返回的 changes、validation、checks 和当前项目版本后，再显式应用：

```sh
chaos assist apply JOB_ID --version CURRENT_VERSION --server http://127.0.0.1:8891 --project workspace
chaos assist cancel JOB_ID --server http://127.0.0.1:8891 --project workspace
```

替换 JOB_ID 和 CURRENT_VERSION 为实际值。应用复用原来的版本比较与冲突保护；不会直接在终端写磁盘源码。目录 IDE 仍按原同步协议将项目变更同步到磁盘。模型文字与未应用提案不等于代码已经修改。

观察超时或断线会给出任务 ID，任务可能继续运行，用 `show`／`events` 恢复观察；不要直接重提同一请求。提交响应丢失时先查 history。Ctrl-C 只对本次 `ask --stream` 创建并确认 ID 的任务请求取消，取消回执不等于已经停止。

运行语言与 `ask` 无需浏览器；项目编程助手需要已有服务。`ask` 只返回回答，不执行回答中的代码。`assist` 可以在私有草稿中解析并受限求值候选代码，使用显式测试 fixtures，不执行真实外部效果；只有显式 `apply` 才修改项目版本。二者都不会自动增加工具权限。
