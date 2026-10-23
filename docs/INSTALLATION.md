# 安装与部署

开始编写 Chaos 程序可直接使用完整 project ZIP；嵌入宿主或开发运行时仍可选择源码、wheel 与 portable SDK 包。语言、命令行和网页服务使用 Python 3.9+；包安装声明 `openai`、`cryptography`、`packaging` 与用于文档渲染的 `markdown-it-py` 依赖。基础语言计算和本地规则示例无需配置 API key。网页资源随包提供，不需要前端构建或 Node.js。

本文的 POSIX 命令适用于常见 macOS/Linux 环境；Windows 使用 `py -3`、对应的 `Scripts` 路径及本机服务管理工具。

## 可直接使用的项目目录

取得可信发布者提供的完整 project ZIP，解压到一个新目录。根目录包含 `chaos.py` 与默认入口 `main.chaos`，初始运行时在 `.chaos/runtime`。不要将它与下方用于嵌入宿主的 portable SDK ZIP 混用；后者的入口是 `run_chaos.py`。

```sh
cd /absolute/path/my-project
python3 chaos.py run
python3 chaos.py check
python3 chaos.py fmt
python3 chaos.py repl
```

`run` 默认执行 `main.chaos`，模板结果为 `42`。纯计算、检查、格式化和 REPL 使用 Python 3.9+ 标准库，可以在没有网络和模型密钥时开始；fmt 输出格式化结果，不覆盖源文件。macOS／Linux 使用 `python3`，Windows 使用 `py -3`。包不包含 Python 本身。

详细的系统检测、彩色进度、自动自检、模型向导、智能建议与持久偏好见[环境检测与配置](DEPLOYMENT.md)。0.153.0 起发行的启动器将 `configure` 和 `setup` 接入同一流程；升级后仍保留更早启动器的项目使用 `setup --yes` 或 `setup --models`，其无参数 `setup` 仍是旧安装入口。

### 放进已有工程

从解压后的 project 包目录执行 install，可以将语言环境放进已有的非空工程：

```sh
python3 chaos.py install /absolute/path/existing-project
cd /absolute/path/existing-project
python3 chaos.py run
```

install 只安装干净运行时与 `chaos.py`、`chaos.project.json`、`main.chaos` 三个模板文件，不复制当前分发目录中的用户源码、设置、密钥、数据库或任务数据。目标已有的其他工程文件保持原样；安装路径或模板文件冲突时拒绝安装。支持 replace 的 Chaos 工程直接用 replace 更新。对于尚不支持此命令的旧项目，从解压后的新版包执行 install 到旧工程可迁移官方启动器并替换环境；已声明兼容环境协议的启动器原样保留；不兼容协议的旧启动器仅在与旧包完全一致时迁移，其他定制入口拒绝覆盖。install 不修改宿主的 README 或 .gitignore；本项目私有 `.chaos/` 及工作区同步元数据应按宿主规则管理，发布环境请使用干净发行包。

### 准备 IDE 与依赖

首次使用完整 IDE 和文档功能时，显式准备项目依赖：

```sh
python3 chaos.py setup --yes
python3 chaos.py
```

setup 使用 pip 的 `--target` 先将依赖装入项目内临时目录，成功后整体启用当前环境的依赖；不向全局 Python 安装；解释器缺少 pip 时，会在临时目录创建安装工具环境，完成后清理。项目运行本身不依赖该临时环境。初始环境依赖位于 `.chaos/deps`，替换后的环境保存自己的匹配依赖。普通运行与打开 IDE 不会自动联网安装依赖。首次安装需要访问配置的包来源；浏览器自动化组件、模型密钥和 GPU 驱动不在 project ZIP 内。可选提供方和设备仍需明确配置与授权，setup 不代表已经连接或调用它们。

无参数启动器进入当前项目 IDE，默认选择本机空闲端口、打印实际地址并打开浏览器；不能假定固定为 8765。自己的入口和模块留在隐藏 `.chaos` 目录之外；运行环境和依赖由 `.chaos` 管理，私有设置保存在 `.chaos/settings.json`，IDE 数据库为 `.chaos/project.sqlite3`。无界面 run 默认使用独立内存数据库；显式 `python3 chaos.py run --persistent` 使用 `.chaos/cli.sqlite3`。CLI 和 IDE 共享源码与设置，不自动共享运行历史。

整个工程可以移动，启动器从自身目录定位活动环境。更换 Python、操作系统或架构后，依赖必须与新平台匹配；按提示重新 setup。不要把用户源码写进 `.chaos/runtime` 或更新创建的版本环境。

IDE 复用原 Workspace 的版本比较、保存和冲突保全机制，与本地源码周期同步。网页和编辑器都改动同一基准时，保留双方与冲突记录，报告并停止自动同步；按报告合并后再继续。周期同步不覆盖编辑器尚未保存的内存草稿，也不是零延迟文件系统事务。

### 直接替换新版本

取得可信的新 project ZIP 或 portable ZIP，关闭本项目的 IDE／后台程序，然后在原工程执行：

```sh
python3 chaos.py replace /absolute/path/Chaos-Project-New.zip
python3 chaos.py status
python3 chaos.py --version
python3 chaos.py run
```

replace 只使用新包里的完整运行环境，不覆盖其中同名的项目模板到原工程。完整包校验和候选语言自检通过后，原子切换活动环境指针；失败时原环境仍可用。运行时和依赖成对保留，依赖需求及 Python／平台指纹相容时可继承，不匹配时按提示显式执行 `python3 chaos.py setup --yes`。用户源码、设置、SQLite 数据库和任务数据不参与替换。

`python3 chaos.py rollback` 回到上一环境及其依赖；随后按原命令重新启动。回退不撤销业务数据、用户源码或配置修改。系统保留当前和上一环境，以及旧布局的基础 `.chaos/runtime` 兼容目录；这些目录只用于管理程序环境，不放用户文件。不要将新 project ZIP 覆盖解压到已有工程。更完整的边界见[更新说明](UPDATES.md)。

replace 使用本地包，不要求更新 feed，也不自动下载或付费调用模型；它与独立服务的托管更新器互不接管。

### 已有安装与发布者

已有 Chaos 安装仍可用原入口创建新项目，或操作指定目录：

```sh
python -m chaos project init /absolute/path/new-project
python -m chaos project setup --directory /absolute/path/new-project --yes --profile core --no-ai
python -m chaos project doctor --directory /absolute/path/new-project --profile core
python -m chaos project run --directory /absolute/path/new-project
python -m chaos project ide --directory /absolute/path/new-project
```

`project init` 只创建三个模板文件，不复制运行时或依赖；setup 在这种工程中检查宿主已安装的依赖，不向项目另装一套。项目命令还可用 `configure --directory DIR --models` 保存模型配置，用 `ask --directory DIR` 读取同一配置，详细选项可在工程外用 `chaos project COMMAND --help` 查看。

全局 `chaos project` 使用当前安装的运行时，`--directory` 选择项目数据。要使用完整目录包自己的活动版本，运行目标工程的 `chaos.py`，它不允许通过 `--directory` 切换到其他工程。要交付不依赖全局 Chaos 安装的完整工程，使用上方 project ZIP 的 install。不要把仅有三个模板的目录当作完整可复制环境。

发布者可从完整 wheel 创建 project ZIP：

```sh
chaos package project /absolute/path/chaos_intelligence-X.Y.Z-py3-none-any.whl --output /absolute/path/my-project.zip
```

`X.Y.Z` 需替换为实际 wheel 版本。项目包使用原语言、CLI、服务和资源；目录启动器只统一项目路径、环境选择和同步入口，没有另一套解释器。取得包、安装到工程、准备依赖和执行程序是不同操作；清单与校验值用于检查内容完整性，发布来源仍须可信。

目录项目需要模型或编程助手时，按[模型配置](MODEL_CONFIGURATION.md)确认本项目设置与环境变量来源。纯计算无需 key；setup 会检测环境、准备依赖、自检并保存设置；默认不验证模型账号或操作设备。

### 项目内的文件与活动环境

```text
my-project/
  chaos.py                  稳定启动器
  chaos.project.json        默认入口声明
  main.chaos                自己的程序
  src/                      自己的模块（按需创建）
  .chaos/
    settings.json           私有模型与工具设置（配置后生成）
    deployment.json         环境指纹、自检记录与部署偏好
    project.sqlite3         IDE 项目、历史和事件（启动后生成）
    cli.sqlite3             CLI --persistent 的独立历史
    tasks/                  默认任务产物目录
    runtime/                初始完整运行时，保留用于旧布局兼容
    deps/                   初始环境依赖（setup 后生成）
    environment.json        换版后的活动／上一环境指针
    environments/           后续环境的 runtime 与各自 deps
```

后续环境 ID 由系统产生，不在脚本中写死。执行 `python3 chaos.py status`，使用返回的 `runtime` 查找当前包内文档和示例；`dependenciesReady` 只说明依赖指纹匹配，不代表模型账号、浏览器或硬件可用。没有持久设置文件时正常配置即可，不要用空对象 `{}` 手工建立设置文件。

移动或备份自己的工程时，先停止进程，再保留源码、隐藏 `.chaos` 和 `.chaos-workspace.json` 等同步元数据；仅复制运行时不能恢复 IDE 历史。回退仅恢复环境，因此更新前的工程备份仍需单独保留。源码文件与隐藏 `.chaos` 状态目录用途不同。

## 从源码安装

在已取得的仓库根目录创建隔离环境：

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python -m chaos --version
python -m chaos run examples/language-tour/main.chaos
```

`-e` 是开发安装，修改源码后由同一环境读取；部署固定版本时可以改用 `python -m pip install .`。直接运行 `python -m chaos` 仍需环境中存在依赖，不代表首次安装完全离线。

Windows PowerShell：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m chaos --version
```

## 从 wheel 安装

取得可信发布者提供的完整 wheel，核对其随附校验值。下列文件名中的 `X.Y.Z` 是占位符，要换成实际文件名：

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install /absolute/path/chaos_intelligence-X.Y.Z-py3-none-any.whl
chaos --version
chaos docs check
chaos serve
```

安装后可以在源码目录之外运行，Python 模块、网页、文档、示例和 vendor 资源包含在 wheel 中。它不是包含所有第三方依赖的离线安装包；pip 默认仍会从包索引解析和安装依赖。这里没有假设公共 PyPI 上存在可用发布版本，应使用实际取得的源码或 wheel。

## 作为完整包放进其他项目

可复制交付与 pip 安装使用同一份 wheel 运行时。收到可信发布者提供的完整 portable ZIP 后，无需先安装 Chaos；将 X.Y.Z 换成实际版本，解压到尚不存在的宿主项目目录：

```sh
python -m zipfile -e /absolute/path/chaos-intelligence-X.Y.Z-portable.zip /absolute/path/my-project/vendor/chaos
python /absolute/path/my-project/vendor/chaos/run_chaos.py package verify /absolute/path/my-project/vendor/chaos
```

在宿主项目根目录建立 `app.py`，导入路径从这个文件定位：

```python
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor" / "chaos"))
from chaos import Framework

assert Framework.evaluate(
    'import "std/contracts" as C; import "std/generic" as G; import "std/iter" as I;'
    'let Request=C.contract_recursive({Node:C.contract_record({value:"Int",children:C.contract_list(C.contract_ref("Node"))})},"Node");'
    'let base=G.generic_method(G.generic_function("solve"),["Any"],fn(request:Request)->Int{request.children[0].value});'
    'let solve=G.generic_method(base,["Record"],fn(next,request:Request)->Int{next()+2},with_next=true);'
    'fold(fn(total,value){total+value},0,I.take(I.map(solve,[{value:0,children:[{value:40,children:[]}]}]),1))'
) == 42
```

需要把自己的 Chaos 库交付给其他项目时，可将闭合 Program 定义 JSON 放在宿主目录，由显式宿主代码读取后交给 `link_program`，用公开 ProgramBundle 保留每文件方言；语言内对应 `program_link`。该链接器不读宿主文件、不安装依赖、不初始化模块，具体例子见[SDK](SDK.md#在现有-python-项目中复制使用)和[Program 库链接](PROGRAM_LINKING.md)。

运行 `python /absolute/path/my-project/app.py` 即可；不需要原开发目录、Web 服务或模型密钥。若使用 CLI，将源程序保存为宿主项目自己的 `main.chaos`，执行下文的 `run_chaos.py run`。环境配置和业务文件不放进 vendor 包里。

取得 wheel 并已安装 Chaos 的构建者，也可用公开命令生成同一种完整 portable 包：

```sh
python -m chaos package build /absolute/path/chaos_intelligence-X.Y.Z-py3-none-any.whl --output /absolute/path/chaos-X.Y.Z-portable.zip
python -m chaos package extract /absolute/path/chaos-X.Y.Z-portable.zip --destination /absolute/path/my-project/vendor/chaos
python -m chaos package verify /absolute/path/my-project/vendor/chaos
```

目标必须是尚不存在的新目录。完整目录包含 `chaos/`、原 wheel 的 `.dist-info/`、许可证、网页、文档、标准库、`run_chaos.py`、`requirements.txt` 和 `manifest.json`。不要只复制仓库中的 Python 文件。也可直接解压发布者提供的完整 portable ZIP，再把其导入根目录放在项目中；保留原 `chaos` 名称。

纯语言计算仅需要 Python 3.9+ 的标准库。IDE 文档渲染、模型、更新签名等完整功能需要原 wheel 声明的依赖；按包内 `requirements.txt` 安装基础依赖。native／gpu／browser extras 的名称和原始条件保存在 manifest 与 METADATA，不被自动安装。包不包含 Python、第三方依赖、浏览器或 GPU 驱动。

```sh
python -m pip install -r /absolute/path/my-project/vendor/chaos/requirements.txt
python /absolute/path/my-project/vendor/chaos/run_chaos.py run /absolute/path/my-project/main.chaos
python /absolute/path/my-project/vendor/chaos/run_chaos.py serve --db /absolute/path/my-project/data/workspace.sqlite3 --config /absolute/path/my-project/data/settings.json --state-dir /absolute/path/my-project/data/control
```

`run_chaos.py` 只定位自身导入根目录并调用原 CLI，所有参数和接口保持一致。SDK 示例见 [嵌入开发](SDK.md)。项目数据库、配置、密钥和产物留在包外；源码／pip 部署原默认路径保持不变。包内资源从包自身定位，缺失资源不会借用宿主项目的同名 web/docs 目录。

升级时解压到新的版本目录，停止并重启宿主应用后选择新导入根目录；不向旧目录叠加文件，也不热替换已经导入的模块。原托管更新器继续管理独立安装的 wheel，不接管宿主项目的 vendored 包。导出、解压和校验不执行用户程序或安装依赖；清单与 RECORD 校验用于完整性，可信发布来源仍需由使用者确定。必须先解压，不支持直接 ZIP 导入、重命名或嵌套 Python 命名空间。

## 可选浏览器工具

只运行语言、网页或普通模型调用不需要 Playwright。需要 `browser.control` 时，在同一个 Python 环境安装 browser extra 和 Chromium：

```sh
python -m pip install -e '.[browser]'
python -m playwright install chromium
```

上述 extra 命令在源码根目录执行。wheel 用户可安装与应用声明兼容的可选依赖：

```sh
python -m pip install 'playwright>=1.49,<2'
python -m playwright install chromium
```

Linux 主机还可能需要发行版的浏览器系统库；按安装器实际诊断准备。浏览器由服务账号启动，浏览器缓存和文件权限必须对该账号可用。浏览器工具操作独立的无头 Chromium，不需要移动桌面鼠标。

## 可选独立 Codex 后端

`native` 后端直接使用应用配置的模型服务，不需要 Codex。只有选择 `codex` 后端的后台任务需要另行安装独立 Codex CLI 及其 Node.js/npm 运行环境，例如：

```sh
npm install --prefix "$HOME/.local/share/chaos-intelligence/codex" @openai/codex
```

在 `/control` 的运行设置中将 `codexPath` 指向实际安装的可执行文件，例如 POSIX 下的 `$HOME/.local/share/chaos-intelligence/codex/node_modules/.bin/codex`，填入时展开成绝对路径。适配器使用独立临时 `CODEX_HOME` 和应用自己的提供方配置；不读取 Codex App 的二进制、账号或 RPC。语言和图内智能体使用 `native` 以遵守项目工具能力边界。集成细节见 [Harness](HARNESS.md)。

## 本机启动与数据位置

```sh
chaos serve --host 127.0.0.1 --port 8765
```

| 内容 | 默认位置或来源 |
|---|---|
| 项目、源程序、记忆、运行及事件 | 当前目录 `.chaos/workspace.sqlite3`，用 `--db` 指定 |
| 模型提供方、密钥、路由及运行配置 | `~/.config/chaos-intelligence/settings.json`，用 `--config` 或 `CHAOS_CONFIG` 指定 |
| 后台任务状态数据库 | 工作区数据库同级 `control` 目录，用 `--state-dir` 指定 |
| 代码、浏览器和图像任务产物 | 运行设置的 `runtime.workspace`，初始默认当前目录 `.chaos/tasks` |
| 更新器的安装与版本目录 | 按 [更新安装](UPDATES.md) 配置，与上述业务数据分离 |

这里独立服务的相对路径按进程工作目录解释；目录项目 `run`／`repl`／`ask` 的相对 `--config` 则按该项目根目录解释。服务管理器应固定工作目录和绝对路径，避免重启后打开另一份空数据库。一个持久数据库只能有一个活动引擎；其他程序通过 HTTP `Client` 访问已运行服务。

## 无界面常驻部署

`chaos serve` 保持原有前台启动方式。需要关闭终端、网页或编程助手后继续服务时，使用同一包内的 `ServiceSpec` 与 `ServiceManager` 交给操作系统管理；不用另建轮询守护进程，也不依赖 Codex App。macOS 使用当前用户的 launchd，Linux 生成并管理当前用户的 systemd 单元。Windows 没有本接口的原生服务适配器，不能从单元文件生成或模拟测试推断其运行支持。

先选择一个服务身份，并将工作目录、解释器、数据库、设置和后台状态目录全部指定为绝对路径。源工作区使用 `source` profile；已启用签名更新的安装使用 `managed` profile，让操作系统托管已有稳定版本监督器。后者继续使用 `current.json`、版本隔离、退出码 75 更新重启和启动失败回滚，不另建一套更新器。`install` 仅安装当前身份拥有的定义，`start` 才启动；`plan` 只返回配置和启动参数，不写文件、不连接服务管理器。

`source` 服务的更新状态隔离在登记根的 `updaters/<name>/`，不会读取进程继承的托管安装目录或用户全局更新 feed；普通前台启动的默认位置不变。源服务不会自动登记或下载版本，更新此源码需显式停止、更新并重新启动。`uninstall` 保留该隔离目录及日志。Linux 适配器同时核对已加载单元路径、D-Bus 返回的完整类型化启动参数，并拒绝外部 drop-in 覆盖；缺少可用的用户 systemd/D-Bus 或无法确认参数时会拒绝操作。

```sh
chaos service plan --name workspace --profile source \
  --python /absolute/path/venv/bin/python \
  --cwd /absolute/path/project \
  --db /absolute/path/data/workspace.sqlite3 \
  --config /absolute/path/data/settings.json \
  --state-dir /absolute/path/data/control --host 127.0.0.1 --port 8765
```

检查计划后将 `plan` 改成 `install`，随后执行：

```sh
chaos service start workspace
chaos service status workspace
chaos service stop workspace
chaos service uninstall workspace
```

`stop` 卸载该身份的运行任务，避免 KeepAlive 立即重启它；保留服务定义供下一次 `start`。`uninstall` 删除本系统登记且字节仍匹配的服务定义，不删除数据库、设置、任务产物或项目。已有同名外部任务、端口上的其他服务、被修改的定义或符号链接会导致拒绝；不会凭名称接管进程。状态中的 `loaded`、`running`、`ready` 分别描述任务加载、进程和健康结果，不能把安装成功当成运行成功。

Python SDK 与 CLI 调用同一个实现：

```python
from chaos import ServiceSpec, ServiceManager

spec = ServiceSpec(
    name="workspace", profile="source",
    python="/absolute/path/venv/bin/python", cwd="/absolute/path/project",
    db="/absolute/path/data/workspace.sqlite3",
    state_dir="/absolute/path/data/control",
    config="/absolute/path/data/settings.json", host="127.0.0.1", port=8765,
)
manager = ServiceManager()
plan = manager.plan(spec)       # 检查配置；没有系统写入
# manager.install(spec)         # 明确安装后，才能 start("workspace")
# manager.start("workspace")
status = manager.status("workspace")
```

`ServiceSpec.to_dict()` 和 `ServiceSpec.from_dict()` 用于保存与交换同一份规范；不可传入任意 shell 命令或模型环境变量。`ServiceManager(root=..., job_dir=...)` 可为独立安装或受控验收指定登记与任务目录，原生位置由所选平台决定。注入 `runner`、`health` 或 `process` 的契约测试只验证算法，返回 `actualVerified: false`；它们不能证明当前机器已真实安装或启动服务。真实 OS 验收须另外记录任务身份、实际 PID、健康身份和停止后的清理结果。Linux 配置生成与便携测试不等于已在 Linux 主机实测。

相对路径仍按原进程工作目录解释，因此服务规范固定工作目录并保存绝对数据路径。一个持久数据库只能有一个活动引擎；其他程序通过 HTTP `Client` 访问已运行服务。关闭网页不会停止 OS 托管的服务；电脑关机或注销用户会受操作系统的用户服务策略影响。后台任务在异常退出后按既有规则标记中断或要求显式协调，不自动重发不确定的外部动作。

服务账号必须能写入所选数据与日志目录，模型密钥只保存在设置文件中，不写入单元命令行。通过 `/control` 配置 `runtime.workspace` 后，原有 `chaos task ... --server http://127.0.0.1:8765`、`chaos process ...` 和 Python `Client` 仍可直接使用。详细更新流程见[自动检测与一键更新](UPDATES.md)；本节没有授权自动安装系统任务。

这是受信任单用户主机的运行环境。服务没有多人身份认证、租户权限隔离或进程级工具沙箱，代码工具拥有服务账号的文件和网络权限。默认保留回环地址；远程个人访问可用 SSH 端口转发。仅修改 `--host` 或增加反向代理不等于完成多用户安全部署，Host/Origin 校验也不能替代身份认证。

## 升级、回滚与备份

普通源码或 pip 安装可停止服务、安装新包，再使用原有数据参数启动。需要自动检测及受监督的一键切换时，按 [更新安装](UPDATES.md) 先配置真实 HTTPS feed 和可信公钥，再执行 `chaos update adopt`；不要猜测或虚构发布地址。

托管更新把程序放入独立版本目录，业务数据、密钥和产物必须留在安装根之外。服务管理器使用稳定启动器，保持原工作目录与参数。更新会等待活动任务并短暂重启，不承诺零停机。作为 Python 库嵌入的应用仍由宿主包管理流程升级和重启。

备份前停止写入，优先正常停止服务后复制数据库、私有配置、任务状态和产物目录；若在线备份 SQLite，使用其一致性备份机制，不要只复制活跃的主数据库文件而忽略 WAL。配置备份含凭据，应保存在访问受限的位置。项目 JSON 导出便于迁移源程序，但不能替代包含运行历史、记忆与任务数据的完整备份。

## 0.26 程序历史迁移

首次启动新增 program_revisions 表并记录各项目现存当前 Program，不改变已有项目版本或模型配置。无法恢复升级前已丢失的历史源码；接口用 firstAvailableVersion 说明起点。历史与项目保存在同一数据库，备份应包含整个数据库；迁移由现有单运行时租约保护。见[程序历史](PROGRAM_HISTORY.md)。

## 外部编辑器语言服务

安装同一 Python 包后，编辑器可启动 `chaos lsp --stdio --root /absolute/path/to/package --entry main.chaos`。它使用标准输入／输出传输协议，不启动网页服务，也不需要模型密钥或 Codex App。编辑器配置、协议能力及与 workspace 同步的关系见[语言服务器](LANGUAGE_SERVER.md)。

## 检查编译前端

安装后运行 `python -m chaos compile examples/compiler.chaos`（源码目录）可读取当前 portable-ast 编译报告；自定义文件使用自己的路径和可选 `--root`。无须模型 key、Codex App 或 LLVM。当前不是机器码发布命令；详细安装后接口见[编译指南](COMPILER.md)。

## 可选 LLVM JIT

Python 3.10+ 可为实际取得的 wheel 安装 native extra，例如 `python -m pip install '/absolute/path/chaos_intelligence-X.Y.Z-py3-none-any.whl[native]'`，将 X.Y.Z 换成实际版本；源码开发使用 `python -m pip install -e '.[native]'`。当前适配 llvmlite 0.45.x；基础 Python 3.9 安装继续提供解释器，`compile_function(fn)` 在无后端时自动回退。代码分析与安装过程不生成机器码，显式 `target="native"` 的实际调用才可确认后端可用。`auto-flow` 在基础 Python 3.9 上也可执行同步 for／模式的便携 PC CFG；`native-flow` 则严格要求后端。可运行包内 `examples/native-iteration.chaos` 和 `examples/native-composition.chaos`；后者包含真实函数体回调与关键字绑定，无额外服务依赖。在无追踪的实际调用后读取 `compile_flow_info(fn).nativeResumes` 验证原生入口，不能仅凭安装成功或目标名称推断。平台权限、功能范围与验证方法见[原生函数](NATIVE_FUNCTIONS.md)和[原生控制流](NATIVE_FLOW.md)。

## GPU 与 Mac M 系列

Python 3.10+ 可安装 `python -m pip install 'chaos-intelligence[gpu]'`，源码开发使用 `python -m pip install -e '.[gpu]'`。PyTorch 后端按实际可用性选择 CUDA 或 MPS／Metal；CUDA 发行包需要匹配设备和驱动。基础 Python 3.9 继续支持便携 CPU Float32 路径。检测由 `await gpu_devices()` 在后台执行，真正数值运行由 `await gpu_map(...)`／`await gpu_matmul(...)` 返回实际 device；安装成功不代表设备可用。完整精度、错误及取消合同见[GPU 指南](GPU_COMPUTE.md)。

## 可选融合 Metal 编译

`compiler="metal"` 要求 Apple M 系列可用 MPS 设备及提供 `torch.mps.compile_shader` 的 PyTorch（本版验收 2.14）；旧 PyTorch 的原 tensor GPU 计算仍可使用。基础 Python 3.9 的 CPU 功能不受影响。每次编译创建独立后台子进程，安装和使用无须 Codex App 或模型 key。示例运行 `python -m chaos run examples/gpu-metal.chaos`，明确 CPU 示例可在无 GPU 环境验证；硬件示例见[Metal 指南](GPU_METAL.md)。

具名移动设备、Mobile MCP 配置与权限边界见[移动设备与跨设备函数](MOBILE_COMPUTERS.md)。
