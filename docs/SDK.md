# 以 Chaos 为底层构建应用

Chaos 提供 Python 嵌入 SDK、Python HTTP SDK、v1 REST／SSE 接口和可安装的发行包。语言解释器与运行时不依赖 Web 界面；源码、结构 IR 与图形编程共用版本化提交，支持实时同步，见 [实时一致性](REALTIME.md)。

## 先选择调用入口

| 场景 | 入口与下一步 |
| --- | --- |
| 学语言、编辑本地项目 | [快速开始](GETTING_STARTED.md)：`python3 chaos.py run`／`python3 chaos.py` |
| Python 纯计算 | `Framework.evaluate`，不需要启动网页或模型 |
| 注册可信工具、持久项目与运行 | 下方 Framework／Registry 示例；显式管理生命周期 |
| 连接已经运行的服务 | `Client`，使用服务实际URL；不要重复打开服务数据库 |
| 无源码分发 | 下方 executable／独立库入口，仍由原运行时执行 |

目录项目的配置和源码在项目内，CLI 默认 run 使用内存库，`--persistent` 使用 `.chaos/cli.sqlite3`，IDE 使用 `.chaos/project.sqlite3`。它们不自动共享运行历史。用 project 包的 install 可将编程环境加入已有工程；replace／rollback 只管理该项目的运行环境。用 `python3 chaos.py status` 查询活动 runtime，不把 SDK 导入路径固定在初始 `.chaos/runtime`。需要长期稳定的宿主导入路径时使用下方专用 portable SDK 包，由宿主显式选择版本并重启。project ZIP 的根 `chaos.py` 是启动脚本；需要 Python 嵌入时按下一节的完整包导入方式定位真实 `chaos` 包，不把根脚本当作 SDK 模块。

连接模型前阅读[模型配置](MODEL_CONFIGURATION.md)。配置文件、Settings(':memory:') 与环境变量的适用路径须分别确认；公开示例只读取环境变量，真实凭据不进入项目源码或分发制品。

## 在现有 Python 项目中复制使用

[完整可复制包](INSTALLATION.md#作为完整包放进其他项目) 与 wheel、源码使用同一个 `chaos` 运行时、SDK、CLI 和资源定位。下面的 `app.py` 放在宿主项目根目录，包解压到该项目的 `vendor/chaos`：

```python
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor" / "chaos"))
from chaos import Framework, Settings, Registry

assert Framework.evaluate("import \"std/math\" as M; M.sum([20,22])") == 42
with Framework(settings=Settings(":memory:"), registry=Registry()) as app:
    project = app.create_project("embedded", source="input + 1", capabilities=[])
    assert app.start(project["id"], project["version"], 41).result(5) == 42
```

若将完整包的 `chaos/` 和相邻 `.dist-info/` 直接放到宿主的 Python 导入根目录，直接 `from chaos import Framework` 即可，无需添加路径。包名保持 `chaos`；自定义嵌套命名空间没有被支持。相邻 metadata/许可证与代码一起保留。Framework 生命周期、回调权限、数据库独占、HTTP v1 和语言版本合同不变；内存配置仍会读取显式提供的环境选项，生产环境使用宿主自己的 `Settings(path)`。

库源码也可以随宿主项目作为普通定义数据交付。公开 `link_program(definition, library_definitions, *, checkpoint=None)` 纯组装并验证，返回原四字段定义；它不会读取文件或初始化模块。保留逐文件方言时使用原 ProgramBundle 接口：

```python
from chaos import Evaluator, ProgramBundle, link_program
library={"kind":"program","entry":"main.chaos",
    "files":{"main.chaos":'fn solve(x){x["value"]+2};export{solve}'},
    "languageVersions":{"main.chaos":1}}
consumer={"kind":"program","entry":"main.chaos",
    "files":{"main.chaos":'import "vendor/math/main.chaos" as M;M.solve({value:40})'},
    "languageVersions":{"main.chaos":2}}
linked=link_program(consumer, library_definitions={"vendor/math":library})
bundle=ProgramBundle(linked["files"][linked["entry"]], linked["files"], linked["entry"],
                     language_versions=linked["languageVersions"])
assert Evaluator().run(bundle) == 42
```

`Framework.evaluate`／`create_project` 的既有参数没有 language_versions，不要删掉版本映射后声称保留旧方言。持久项目候选通过既有 `validate_program`／`adopt_program` 接收完整定义；原有授权、测试和版本 CAS 不变。实际 AgentTool 库组合见[Program 库链接](PROGRAM_LINKING.md)。

公开包 API：`build_portable(wheel, output)` 验证完整 wheel 并创建不可覆盖的 ZIP；`extract_portable(archive, destination)` 在验证全部路径和文件后独占创建新目录，完成清单最后写入；`verify_portable(root)` 只读校验完整包。它们也从 `chaos` 导出，失败抛 `PackageError`，不自动修改 `sys.path`、安装依赖、读取项目配置或创建业务数据库。

`Framework.evaluate` 直接执行有界语言计算，不启动持久项目或自动提供模型、电脑等宿主服务；需要这些效果时，通过带显式配置和授权的 Framework 项目运行接入原运行时。

Python 代码解释器为子进程明确提供当前 `chaos` 的导入根目录，临时脚本可以调用同一份包；父进程环境、原工作目录、输出和取消合同不变。宿主工具及 shell 仍是受信任主机执行。其他语言通过原 HTTP v1/OpenAPI 接入，不需要另一个执行后端。

## 无源码加载的可执行文件

编译、链接和执行为三个独立步骤。展开后的模块对象可以作为 JSON 放进宿主项目目录，部署时无需项目的 `.chaos` 源码：

```python
import json
from chaos import compile_objects, link_objects, load_executable, Framework

objects = compile_objects('import "math.chaos" as M; M.square(input)',
    files={"math.chaos": "fn square(x){x*x}; export{square}"})
image = link_objects(objects)
blob = json.dumps(image, ensure_ascii=False)
# 后续进程仅需运行时与 blob；不重新解析源码或展开宏。
assert Framework.evaluate(load_executable(json.loads(blob)), {"input": 7}) == 49
```

`compile_executable` 是编译与链接的便捷入口；四个函数也作为 `Framework` 静态方法公开。省略 `libraries` 时，`compile_objects` 保留原 v1 封闭源码包合同；显式提供 `libraries` 则采用下方独立库合同。`link_objects` 校验所有对象及依赖闭包，包含未执行模块。宏依赖在显式客户端编译阶段完成展开；v3 库可携带独立纯宏接口，旧 v1／v2 默认不变。每次 `load_executable` 都重建全新的语法节点和静态优化计划，不保存活闭包、Task、权限或设备租约。

```sh
chaos executable build app/main.chaos --root app > app.cxe.json
chaos executable build app/main.chaos --root app --objects > app.objects.json
chaos executable link app.objects.json --entry main.chaos > app.cxe.json
chaos executable validate app.cxe.json
chaos executable run app.cxe.json --input-json 7
```

### 在其他工程使用预编译库

发布者先构建模块对象，使用者仅保存 JSON 与自己的源码：

```python
import json
from chaos import compile_objects, compile_executable, load_executable, Framework

# 发布者：显式空列表启用独立对象 v2。
library = compile_objects('fn square(x){x*x}; export{square}',
                          entry='math.chaos', libraries=[])
library_blob = json.dumps(library)
# 使用者：这里不需要 math.chaos 的源码或文件。
image = compile_executable('import "math.chaos" as M; M.square(input)',
                           libraries=json.loads(library_blob))
assert Framework.evaluate(load_executable(image), {'input': 7}) == 49
```

预编译库由本节 SDK／CLI 构建入口显式提供；现有 IDE 源码项目的解析和补全仍以其源码文件集为依据。构建后的镜像可使用网页所连接的原 HTTP 运行服务。

`libraries` 是模块对象列表；多个库可用列表相加传入。重复的完全相同模块可共享，冲突内容、缺失依赖、路径重叠和循环会被拒绝。源码文件与库模块不能同名。结果仍是包含库和本地模块的完整对象列表，方便整体保存；没有暗中访问包仓库或库源码。模块路径沿用原 `import` 规则，相对于导入文件解析；普通构建不会自动重命名。不同库的原始模块同名时，先用显式挂载得到互不重叠的逻辑目录。

```sh
chaos executable build vendor/math.chaos --root vendor --objects > math.objects.json
chaos executable build app/main.chaos --root app --library math.objects.json > app.cxe.json
chaos executable run app.cxe.json --input-json 7
```

`--library` 可重复指定，输入为对象列表 JSON；旧 v1 封闭对象会先完整验证再显式转换元数据。依赖库不经源码解析或宏展开，构建期间不执行初始化。v2 对象绑定每个直接依赖的精确内容哈希；库实现变更后必须重新编译客户端，不支持任意实现的免重编替换。

`mount_objects(objects, prefix)` 和 `Framework.mount_objects(objects, prefix)` 将完整编译库挂载到规范的非 `std` 相对目录，返回新的、已验证的 v2／v3 对象列表。v1 输入先按原合同验证并升级为 v2；含 v3 的输入保留阶段接口。调用不需要库源码，不运行 Parser、宏展开或模块初始化，也不修改输入列表。标准模块不搬迁，重复的相同标准对象仍由原库组合规则共享。

```python
from chaos import mount_objects, compile_executable
left = mount_objects(left_library_objects, "vendor/left")
right = Framework.mount_objects(right_library_objects, "vendor/right")
image = compile_executable(
    'import "vendor/left/main.chaos" as L;'
    'import "vendor/right/main.chaos" as R;[L.answer(),R.answer()]',
    libraries=left + right)
```

```sh
chaos executable mount left.objects.json --prefix vendor/left > left.mounted.json
chaos executable mount right.objects.json --prefix vendor/right > right.mounted.json
chaos executable build main.chaos --library left.mounted.json --library right.mounted.json > image.json
```

挂载仅接受模块来源可以明确重定位的已验证对象；缺失必要导入／data 定义来源的人工 IR 不靠名称猜测。它改变非标准模块的逻辑身份和内容哈希，原相对导入、精确依赖绑定及宏定义位置同步迁移；`sourceHash` 仍指向原发布源码字节，不伪造挂载后的源码，也不证明 IR 与源码等价。用户字符串、参数标签、卫生别名和数值不被替换。两份挂载拥有独立模块状态，不能借相同名称共享可变 Cell；同一挂载内部仍按原导入缓存只初始化一次。新前缀须在原路径、对象数和总大小预算内；目录冲突或陈旧依赖仍在执行前拒绝。它不进行依赖版本求解、下载、动态库替换或权限授予。

运行时导出的函数、类、数据、Agent／AgentTool 使用原调用与宿主权限合同。发布纯宏接口时显式传 `macro_interfaces=True`；CLI 对应 `--macro-interfaces`。客户端的 `libraries` 只要含 v3 对象，就自动以 v3 编译并可调用导出的宏，无需库源码，客户端自己声明的宏也保留在其 v3 阶段接口中供下游复用。`macro_interfaces=True` 只是在没有 v3 输入时显式选择该格式。私有宏仍不可从外部调用；库内部已展开宏、客户端自己的宏和定义处的私有帮助宏保持原卫生与阶段语义。旧 v1／v2 对象缺少宏实现时，外部调用继续报 `macro_error`。

```python
library = Framework.compile_objects(
    'macro increment(x){quote{unquote(x)+1}};export{increment}',
    entry='macros.chaos', macro_interfaces=True)
image = Framework.compile_executable(
    'import "macros.chaos" as M;M.increment!(41)', libraries=library)
assert Framework.evaluate(Framework.load_executable(image)) == 42
```

```sh
chaos executable build macros.chaos --objects --macro-interfaces > macros.json
chaos executable build main.chaos --library macros.json > image.json
chaos executable run image.json
```

宏编译只运行原纯 Expander，不获得运行时工具／模型／Robot 权限。合法但未调用的宏体不提前求值；调用时的纯度、Syntax 结果、共享步数／节点／深度／时间限制仍生效。`link`、`validate` 和 `load_executable` 不展开宏，不运行模块初始化。内容依赖包含阶段定义，库宏实现改变后必须重新编译客户端。

### 执行与宿主权限

`executable run` 与 `Framework.evaluate` 是有界纯计算入口。嵌入宿主可将 `load_executable(image)` 传给原 `Evaluator.run`，并通过 Evaluator 已有的显式 handler 接入 Agent／工具／Robot；文件本身不提供这些服务或权限。持久编译文件运行使用下方 start_executable 入口，复用原 RunHandle／SSE／Bot 指导。动态 `Program`、`module_from`、`eval_code` 创建新源码时仍正常使用前端。

持久运行与源码项目共用权限、并发限制、事件、指导邮箱、取消和资源清理：

```python
from chaos import Framework, compile_executable
image = compile_executable('input * input')
with Framework('runtime.sqlite') as app:
    project = app.get_project('workspace')
    run = app.start_executable(project['id'], project['version'], image, 7)
    assert run.result() == 49
```

`Client.start_executable` 接口相同；HTTP 在原 `POST /api/v1/runs` 请求中添加 `executable: image`，可省略 mode 或指定 executable，autoOptimize 必须为 false，debug 必须省略或为 null。trace、input／inputJson、RunHandle.guide／cancel／watch 沿用原合同。校验完整镜像后，在创建运行的同一数据库事务内重新检查项目版本和权限；请求不能附带能力授权。

```sh
chaos executable start app.cxe.json --db runtime.sqlite --project workspace --input-json 7
chaos executable start app.cxe.json --db runtime.sqlite --stream
chaos executable start app.cxe.json --db runtime.sqlite --interactive
```

`start` 不保存或覆盖项目源码。`--config` 选择模型和运行时配置；`--devices`／`--robots`／`--computers` 接入原注册表，能力仍由已有项目授权。终端交互与 `chaos run --interactive` 使用同一协议和取消清理；不提供脱离本地执行生命周期的 detach。若要关闭终端后继续，使用运行中的服务器或既有持久 Process 合同。

快照中的 `execution.imageHash` 和完整 `execution.image` 标识实际代码；项目 source／files 只保留宿主授权上下文，界面和运行图按镜像显示。文件无原始源码，`current_program()` 明确报 program_error，不能借宿主源码伪造 Program 或采用权限；显式 `program_from` 仍可创建并运行源程序。Goal／录制按实际代码身份隔离。运行重启后标记 interrupted，不自动重放已发生的效果。源码断点调试暂不适用于镜像。

本地镜像核心上限 16 MB；HTTP 请求保持 4 MB，Client 响应保持 8 MB。大型本地快照不能保证通过远程传输。摘要哈希标识内容，不证明签名或来源可信。运行事件展示公开计划／工作摘要与真实动作证据，不暴露隐藏推理。


这是在 Chaos 运行时执行的可移植中间表示，不是机器码 AOT。显式 JIT 可继续从已加载 AST 编译控制流。对象哈希用于内容身份和损坏检查，不证明源码等价，也不是作者签名或授权。旧 `artifact` 接口仍保留源码重验证合同；两种格式互不冒充。

## 安装与独立运行语言

```sh
python3 -m pip install .
chaos --version
chaos run examples/counter-machine.chaos
chaos check examples/counter-machine.chaos
printf 'fn square(x) { x*x }; square(input)' | chaos run - --input-json 7
chaos serve --port 8877 --db application.sqlite3
```

从源码也可用 `python3 -m chaos run ...`，完全不需要启动 HTTP 服务。`run` 输出 JSON，失败写 stderr 并返回 1；`check` 验证语法、模块依赖与导出，不执行程序，也不承诺静态类型或终止性证明。默认数据库在内存中，指定 `--db` 可保留运行和记忆；脚本会更新该数据库的 `workspace` 源码。CLI 默认仅使用内置工具；扩展以 Python SDK 明确注册。

### 实时事件与运行中指导

```sh
chaos run task.chaos --stream
chaos run task.chaos --interactive
```

`--stream` 按持久事件 ID 顺序立即输出 JSON Lines，包含同一 Run 的状态、公开计划／进度摘要、工具事件与指导收据；成功后追加 `{"type":"run.result","runId":"…","output":…}`。失败仍返回非零状态并写 stderr，已有事件保留，不伪造成功结果。默认不加参数的 `run` 继续只打印最终 JSON。事件是公开工作记录，不是模型隐藏推理。

`--interactive` 使用同一事件流和指导邮箱，在 `guide>` 输入自然语言并回车。提交回执不代表已采用：Bot 在既有指导检查点接收并应用，后续决策和事件可核对。普通计算或没有指导消费点的程序不会因终端输入自动改变语义。该模式需要 POSIX 的 stdin/stdout 都是真实终端，不能与 `run -` 的标准输入源码同时使用；管道与其他平台使用 `--stream` 或 SDK／HTTP。

本地 `run` 拥有执行生命周期：`/cancel`、Ctrl-C 或 Ctrl-D 请求取消并等待原运行时清理；取消／中断输出带实际状态的 `run.exit` 并返回 130，不作为成功结果。此模式不提供把本地活动栈遗留到后台的 `/detach`。若需要关闭终端后仍持续工作，使用服务器持久 Process 的 `watch`／`attach`；它们的断开只结束观察。`/help` 显示控制，`/retry` 仅复用最后一个投递结果不确定的消息 ID，避免把重试变成重复指导。能力、共享运行预算、资源租约和幂等语义均不因实时输出而扩大。

安装包包含界面资源，能在源码目录之外启动。Python 3.9+；模型框架依赖 openai Python 包，浏览器工具通过 browser extra 安装 Playwright。构建工具使用 setuptools 和 wheel；安装请使用实际取得的源码或可信 wheel，见 [安装与部署](INSTALLATION.md)。

## 1. 注册宿主工具和智能体提供方

```python
from chaos import Registry, ToolCall, Final

registry = Registry()

@registry.tool("app.double", input_type="Int", output_type="Int", effect="pure")
def double(value, context):
    context.checkpoint()  # 检查取消和共享运行预算
    return value * 2

@registry.provider("app.planner")
def planner(request, context):
    if request.step == 1:
        return ToolCall("app.double", request.input)
    return Final({"answer": request.history[-1]["output"]})
```

提供方每次调用只提出一个动作；运行时检查工具声明与项目授权，执行工具，把观察加入 history，然后进行下一步。`Final(False)`、`Final(None)`、`Final([])` 都是合法结果。提供方必须在 `maxSteps` 内返回 Final；动作无限循环会明确失败。

也可以使用 `Registry.register_tool(ToolSpec(...))` 和 `register_provider(ProviderSpec(...))`。输入／输出契约采用 Chaos 的 JSON 数据类型：`Any, Int, Number, String, Bool, List, Record, Null`。metadata 可声明 description、label、version、effect；宿主代码的 pure 声明是其承诺，不由 Python 沙箱证明。

`AgentRequest` 包含 name、prompt、input、step、max_steps、tools 描述和 history。`ExtensionContext` 只有 project_id、run_id、node_id、deadline、cancelled 和 checkpoint，不向回调公开数据库或引擎。

回调是受信任宿主 Python，不是安全沙箱；不合作的函数不能被线程强制中断。参数、观察、返回值均在边界复制并检查为有界 JSON；会拒绝循环对象、函数、非有限浮点、无效 Unicode 和过大整数（14,000 bit）。注册表在创建框架时快照，后续注册不改变运行中的语义；重复名称和保留的 graph.patch 权限不能覆盖。HTTP 不接受 Python 代码注册、任意 import 或工具可执行路径。

## 能力分类与设备发现

`Framework.capabilities()` 和 `Client.capabilities()` 保留原能力接口，工具描述增加 `scope`（`tool`、`language`、`platform`）和 `callable`。`devices` 按注册名列出 `DeviceSpec.describe()` 的静态名称、描述、版本与 profile；不会调用工厂，不包含物理路径、资源标识或活句柄。注册不是授权；原生设备仍须项目和角色共同允许 `device.control`。

`edit_program(..., capabilities=None)` 继续使用原 `/projects/{id}/program` 接口。省略权限参数保留现有授权；显式列表可与源码／IR 在原 `Store.save` 版本事务中一起保存，空列表撤销授权。版本冲突或非法字段拒绝整个提交，不先改代码再补权限。普通 `save_project` 参数和接口地址不变。

## 2. 构建多智能体应用

```python
from chaos import Framework, Workflow

flow = (Workflow()
    .input()
    .agent("solver", "把输入翻倍", provider="app.planner", tools=["app.double"])
    .output()
    .connect("input", "solver")
    .connect("solver", "output"))

with Framework("application.sqlite3", registry=registry) as application:
    project = application.create_project(
        "doubling", graph=flow, capabilities=["app.double"])
    run = application.start(project["id"], project["version"], 21)
    assert run.result(timeout=5) == {"answer": 42}
    for event in run.watch():
        print(event["type"])
```

`Workflow.function(id, source, tools=...)`、`.tool(id, registered_name)`、`.agent(...)` 使用和画布完全相同的 JSON 节点。`.connect(..., port="analysis")` 声明多输入字段；`.build()` 默认要求完整路径，`.build(complete=False)` 导出草稿。父节点须先声明，`parent=...` 与 `level=...` 表示严格层级。

工具授权分两层：项目 capabilities 允许应用使用哪些工具，agent/function 的 tools 再缩小自己的集合。工具节点也受项目授权限制。平台修改仍经过独立的 graph.patch 能力、管理域、路径与版本验证。

示例 [support_assistant.py](../examples/support_assistant.py) 用公开 SDK 实现查单与回复两个智能体，宿主订单工具通过动作协议调用；两个提供方是明确标注的确定性示例，可以替换为真实模型适配器。

## 3. 项目、版本、运行与生命周期

- `create_project / projects / get_project / save_project` 管理多个项目。项目隔离覆盖记忆、运行、事件、源码和图，**不等于多租户鉴权**。
- `save_project(project)` 携带 version；成功返回新版本，过期抛 Conflict。SQLite 写事务保护跨连接的比较和提交。
- `start(project_id, version, input, mode="source"|"graph", auto_optimize=False)` 返回 RunHandle。SDK 默认不自动改变图；显式启用 auto_optimize 或调用 optimize。
- `RunHandle.get / wait / result / cancel / watch` 提供运行观察；`wait` 返回任何终态记录，`result` 在非 completed 时抛 RunFailed，其 `.run` 保留证据。
- 等待或观察超时不会隐式取消任务；`cancel` 是独立动作。取消后运行输出为 null，丢弃迟到结果；已提交的工具副作用不会回滚。
- run 的 snapshot 保存执行当时的完整项目及能力清单。读取事件是审计回放，不会重执行工具；当前 SDK 不提供自动重新执行历史快照。
- 同一个数据库只能有一个活动引擎；同进程服务应传入现有 Framework，其他进程使用 HTTP Client。重复打开会失败，不会把其他实例的活动运行误判为重启中断。
- `close(timeout=5)` 拒绝新任务，请求取消运行与平台规划，再等待回调退出；超时保留数据库与独占锁，调用方可待回调结束后再次 close。关闭完成后才能安全重新打开。

取消请求与终态提交使用同一个互斥边界：`run.cancel()` 返回 `True` 表示请求已被接受，随后提交终态时会选择 `cancelled` 并丢弃迟到结果；终态先提交时返回 `False`。这不强制中断正在执行的受信宿主回调，回调仍须配合 checkpoint。

同一运行期内，原调度器确认终态 Task、Generator 或 AsyncGenerator 的执行运输已经关闭、帧已排空且没有活动子运输后，释放已消费的调用参数、捕获输入和 Runner 动态上下文引用。句柄本身、终值、完整多结果、原异常载荷及调度元数据仍保留，因此重复 await、完成后取项与多值读取不重放主体。结果或异常仍引用的值不会因此释放；运行时也不裁剪原异常 traceback。

冷取消／关闭不启动用户代码，活动等待和借用句柄的生命周期不变。未确认排空的运输继续保守保留；不会为了释放输入而运行、关闭或消费借用目标。终态句柄仍属于原调度器登记表，每 epoch 的原任务／生成器数量和捕获图上限不变。这是原运行时的引用生命周期收束，不增加公开 API，也不提供全堆 GC、无限长活 epoch 或跨重启恢复活栈。

工作线程会把工具或提供方的异常退出（包括 `SystemExit`）收束为失败记录，避免留下失去工作线程的“运行中”任务。错误写入运行记录和事件前先按已配置密钥脱敏，再截断长度。宿主资源清理失败不会覆盖原始业务错误或硬控制信号；仅清理失败时，运行报告失败。

事件与运行查询使用单调游标：`events(project_id, after=0, limit=100, run_id=None)` 和 `runs(project_id, after=0, limit=100)` 返回 `{items, nextCursor}`。项目列表使用字符串 id 游标。nextCursor 非 null 时继续查询，单页最多 300。watch 自动读取所有分页；它不是 UI 的最近 30 条历史窗口。

## 4. 在已有应用中托管 API

```python
from chaos.server import make_server

application = Framework("application.sqlite3", registry=registry)
server = make_server(port=8877, framework=application)
try:
    server.serve_forever()
finally:
    server.server_close()  # 借用模式不关闭 application
    application.close()
```

需要从另一个线程停止服务时调用 `server.shutdown()`，再 `server_close()`。不传 framework 时，服务自己创建并关闭引擎。自定义注册表只能由可信的宿主启动代码提供。

## 5. HTTP SDK

```python
from chaos import Client, ConflictError

client = Client("http://127.0.0.1:8877")
project = client.get_project("doubling")
run = client.start("doubling", project["version"], 21)
print(run.result(timeout=10))
for event in run.watch(timeout=10):
    print(event["id"], event["type"])
```

Client 与 Framework 的项目、图、运行和事件方法保持相同形状。类型化 HTTP 错误为 ValidationError(400)、NotFoundError(404)、ConflictError(409)，其他状态为 APIError；均有 status、code、details。普通连接失败为 TransportError，等待截止时间导致的超时为 TimeoutError。SDK 禁止重定向，不会悄悄把请求发往另一个服务。

默认服务是本机开发接口，仍有 Host/Origin 检查。外部部署的鉴权、TLS 终止和多租户安全策略不在此版本中提供。

## 6. API v1

机器可读契约：`GET /api/v1/openapi.json`（OpenAPI 3.1）；能力发现：`GET /api/v1/capabilities`。完整路由、错误与并发约定见 [HTTP API](HTTP_API.md)。

| 方法 | 路径（前缀 /api/v1） | 作用 |
|---|---|---|
| GET | /health | 版本与存活状态 |
| GET / POST | /projects | 列表／创建项目 |
| GET / PUT | /projects/{projectId} | 读取／按版本保存 |
| POST | /projects/{projectId}/validate | 结构与注册能力预检 |
| POST | /projects/{projectId}/patch | 原子补丁 |
| POST | /projects/{projectId}/optimize | 平台调整策略 |
| POST | /validate | 图及可选源码语法验证 |
| GET / POST | /runs | 分页运行列表／提交运行 |
| GET | /runs/{runId} | 状态、快照和结果 |
| POST | /runs/{runId}/cancel | 请求协作取消 |
| GET | /events | 项目／运行事件分页 |
| GET | /events/stream | SSE，支持 Last-Event-ID |

runs 列表与 events 需要 projectId 查询参数；事件可增加 runId。创建返回 201、运行提交返回 202；错误统一为 `{error: {code, message, details}}`。版本只能是正整数，布尔和浮点不能替代。错误方法返回 405 和 Allow，旧 `/api/*` 界面兼容入口继续可用。


## 7. 多文件语言程序

```python
with Framework() as app:
    p = app.create_project('modules', source='import "lib.chaos" as L; L.double(input)',
        files={'lib.chaos': 'fn double(x){x*2}; export{double}'})
    assert app.start(p['id'], p['version'], 21).result() == 42
    p = app.edit_program(p['id'], p['version'], file='lib.chaos',
        source='fn double(x){x*3}; export{double}')
    ir = app.program(p['id'])['program']
```

Client exposes the same methods. Program bundles use canonical relative .chaos paths; imports never read server files. Project source mirrors files[entry]. save_project(..., from_view='source' or 'graph') selects explicit edit intent when both drafts changed. Source and graph run modes execute the same canonical program. GET/POST /api/v1/projects/{id}/program reads/edits IR; POST accepts exactly one of source, program or operations plus a version and optional file. GET /api/v1/language describes shapes, templates and standard modules. All edits use optimistic version checks.

ProgramBundle, Session, parse_program, format_program and patch_program are public language APIs. 作用域与边界见 [语言参考](LANGUAGE.md)。 Migration keeps old independent source in legacySource and converts the old graph to workflow source.

公共接口包括 `Client.snapshot` / `Framework.snapshot`、公共 `Workspace` 与 `WorkspaceConflict`、`start(..., trace=True)`。HTTP 新增 snapshot、inputJson/outputJson 与有界源码事件；见 [实时同步约定](REALTIME.md)。


控制平面支持 Client.settings/configure/discover_models/start_task/task/task_events/cancel_task/resume_task。嵌入 Framework 默认使用内存配置，不读取默认设置文件；兼容环境变量 `CHAOS_MODEL_URL`／`CHAOS_MODEL_KEY`／`CHAOS_MODEL_NAME` 由 Settings.resolve 统一临时处理，不重建配置或替换宿主 client_factory；移除变量后恢复持久路由，具体智能体覆盖优先。模型发现与公开配置页仍展示保存的配置。多入口共用模型时按[模型连接教程](MODEL_CONFIGURATION.md)显式持久化，避免混淆保存值与临时值。要采用部署配置，显式传 `Framework(settings=Settings())`，或用 `Settings(path)` 指定宿主自己的配置文件。独立 CLI 和服务会读取部署配置。见 [HARNESS](HARNESS.md)。


普通 SDK／CLI 消费者能运行安装包中的 `examples/native-iteration.chaos` 与 `examples/native-composition.chaos`：原 `compile_function(fn, target="auto-flow")` 将普通函数的控制结构纳入续接 CFG，真实函数体调用、参数模式与受保护子树沿原调度器执行；无 LLVM 时执行同一便携路径。需要验证实际机器码入口时使用 `native-flow` 并读取 `compile_flow_info` 的真实计数；项目运行传 `trace=False` 才允许原生派发。原 Function 可以直接绑定 AgentTool／Team，不需要独立编译服务。详见[原生结构控制流](NATIVE_FLOW.md)。

更多安装、架构、可执行插件示例与测试流程见 [开发者指南](DEVELOPER.md)。文档入口为 [文档中心](README.md)，常见错误见 [故障排查](TROUBLESHOOTING.md)。

## 8. 函数调用扩展与结构集成

默认参数、具名调用、同步 `*` 迭代展开 / `**Record` 展开及 `apply/partial/compose` 是语言内部调用规则；`Framework.start(project_id, version, value)` 与 HTTP 输入仍传递一个 JSON 值，不改变现有 SDK 调用形状。源程序可以把输入记录拆成具名参数：

```python
from chaos import Framework

source = "fn total(a: Int, b: Int = 2, *, scale: Int = 1) { (a + b) * scale }; apply(total, [], input)"
assert Framework.evaluate(source, {"input": {"a": 19, "scale": 2}}) == 42
```

语言智能体可写 `worker(input=value)`，data 构造器可以按声明字段名称调用。具名参数不会变成额外的模型工具权限，工具和提供方回调仍遵守现有 `handler(value, context)`、`handler(request, context)` 协议。

IR 中旧式 `parameters: [{name, type}]` 仍有效。扩展参数记录增加 `mode` 和 `default`；`mode` 为 `positional / keyword_only / varargs / varkw`，`default` 为表达式节点或无默认值时的 `null`。默认值为语言 `null` 时使用字面量节点。调用参数中新增 `keyword / spread / kwspread` 节点。编辑器应保留参数模式、默认表达式及展开节点，通过公共 `parse_program`、`format_program` 和 `patch_program` 完成校验与往返，不要只修改渲染文本而丢弃默认表达式。完整语义见 [语言参考](LANGUAGE.md)。


## 9. IDE 与程序包操作

`Framework.analyze(source, *, file="main.chaos", files=None, entry="main.chaos", position=None, language_versions=None)` 返回不执行程序的语言分析，位置使用 UTF-16 单元。`Framework.format_source(source, *, file="main.chaos", language_version=2, line_width=88, indent_size=2)` 返回保留注释且语义一致的源码。远程 `Client` 提供相同方法。

分析响应增加 `outline`，与原 `symbols`、定义位置及提示并存。它为 `{file, complete, truncated, items}`；每个节点含 `name`、`kind`、`detail`、UTF-16 `range`／`selectionRange` 和 `children`。范围描述原始声明与名称，层级为语法容纳关系；未完成源码和限制通过状态显式表达，未知动态类型仍保守。它不执行代码、展开引用模板或推断整个工程的调用图。编程助手从经过校验的冻结输入推导并截取同源大纲，客户端无需提交自造 AST。

`edit_files(project_id, version, operation, path, *, new_path=None, source=None, directory=False)` 同时存在于 `Framework` 和 `Client`，返回新项目；重命名会更新真实导入。操作为 `create`、`rename` 或 `delete`。目录创建会添加 `__init__.chaos`，删除入口/现存依赖会失败；所有提交都使用版本检查。

HTTP `Client` 还提供以下后台对话方法：`start_assistant(project_id, version, prompt, *, session_id=None, editor_context=None)`、`assistant_history(project_id, *, session_id=None)`、`assistant(project_id, job_id)`、`assistant_events(project_id, job_id, *, after=0, limit=200)`、`cancel_assistant(project_id, job_id)`、`apply_assistant(project_id, job_id, version, *, editor_context=None)`。启动返回任务及 `sessionId`；轮询/事件读取不自动应用提案。应用方法返回项目，版本冲突抛 `ConflictError`。

`editor_context` 可选记录包括 `files`、`entry`、`languageVersions`、`activeFile` 和 `selection: {anchor, head}`；选区按 UTF-16 单元，允许反向选择。发送的是完整当前源码草稿，不隐式保存；基准 `version` 仍为该草稿原先读取的项目版本。返回任务的 `editorContext` 是有界来源元数据，`draftHash` 对文件内容、入口和方言计算，不包含当前文件或选区。

带上下文任务的 `changes` 比较输入草稿与生成候选，应用时必须再次传当前完整 `editor_context`；前端检查本地草稿变化，服务校验应用请求快照的内容身份及原版本，不一致就拒绝旧提案；请求发出后继续编辑的本地内容由前端保留。未传上下文的旧调用保持原已保存项目语义和请求字段。对话使用服务端模型配置，客户端不传模型密钥。工作流、草稿与冲突处理参见 [IDE 指南](IDE.md)。

## 10. 交互式源码调试

`Framework` 与远程 `Client` 均提供：

```python
run = app.start_debug("research", version, input_value,
    breakpoints=[{"file": "main.chaos", "line": 5}],
    pause_on_entry=True, pause_timeout=300)
import time
until = time.monotonic() + 10
state = app.debugger(run.id)
while state["status"] == "running" and time.monotonic() < until:
    time.sleep(0.05)
    state = app.debugger(run.id)
if state["status"] != "paused":
    run.cancel()
    raise RuntimeError("没有在期限内暂停")
app.debug_command(run.id, "step_into", pause_id=state["pauseId"])
# 后续每次暂停重新读取 pauseId；也可 continue / step_over / step_out。
run.cancel()
```

开始调试固化项目版本并使用 source 模式；`RunHandle.debugger()` 和 `RunHandle.debug_command(...)` 提供相同操作。普通 `start` 的可选 `debug` 参数接受 HTTP 同形配置，既有调用方式保留。断点文件必须属于运行快照；恢复命令必须匹配当前暂停编号。项目编辑、运行生命周期和调试状态是三个不同概念。

详见 [调试器](DEBUGGER.md)。宏和挂起函数经过同一 ProgramBundle/IR；SDK 不需要把它们翻译成另一种执行语言。


### 情况与恢复调试

Framework／Client 的 `start_debug` 新增可选 `pause_on_condition=False`，普通 start 的 debug 配置接受 `pauseOnCondition`。RunHandle／Framework／Client 原 `debug_command` 增加可选 `restart_id`、`args`、`kwargs`，已有继续、单步和断点参数保持原调用方式。默认关闭时不发送新配置字段；RunHandle 也不向既有后端传递未提供的新关键词，保留旧服务与嵌入后端的默认调用合同。显式启用情况调试需要支持此能力的服务。

```python
run = app.start_debug(project["id"], project["version"],
                      pause_on_entry=False, pause_on_condition=True)
import time
deadline = time.monotonic() + 5
while time.monotonic() < deadline:
    state = run.debugger()
    if state["status"] == "paused" and state["reason"] == "condition":
        break
    if state["status"] not in {"running", "paused"}:
        raise RuntimeError("运行已结束，未取得情况暂停")
    time.sleep(0.01)
else:
    raise TimeoutError("等待情况暂停超时")
recovery = state["conditionRecovery"]
restart = recovery["restarts"][0]  # 由调用者依据纯数据明确选择
run.debug_command("restart", pause_id=state["pauseId"],
                  restart_id=restart["id"], args=[41], kwargs={})
```

快照只含有界情况／出口描述，不能把它反序列化为 Restart。HTTP 与 GUI 使用同一命令路径，最终回调仍在原解释器的 holder、能力和版本中执行。`continue` 拒绝这次情况恢复；取消、超时及不可确定的外部副作用不能借恢复转成成功。细节见[情况合同](CONDITIONS.md)与[调试器](DEBUGGER.md)。

## 11. 流程序与编译期导出

Framework/Client 的 `start` 及文件/IR 编辑接口不变。运行输入输出仍是有界 JSON；Task、Generator、AsyncGenerator 的描述不能作为可恢复执行栈传输。ProgramBundle.summary 的 `exports` 保留运行期名称，`macroExports` 列出显式公开的编译期宏。

```python
from chaos import Framework
source = 'import "forms.chaos" as F; async fn* values(){yield F.identity!(40);yield 42};await acollect(values())'
files = {'forms.chaos': 'macro identity(x){quote{unquote(x)}};export{identity}'}
assert Framework.evaluate(source, files=files) == [40, 42]
```

源码调试、SDK和网页仍读取同一保存版本。异步流内部的已注册智能体和工具经过标准 Context，不需要第二套 provider 接口。

## 12. 跨模块对象组件

SDK 与 CLI 使用相同的类身份、限定类型契约和调用协议：

```python
from chaos import Framework

files = {"components.chaos": "class Adder {var base:Int=0; fn __init__(self, base){self.base=base;null}; fn __call__(self,x){self.base+x}}; export{Adder}"}
source = 'import "components.chaos" as C;fn invoke(value:C.Adder){value(2)};invoke(C.Adder(40))'
assert Framework.evaluate(source, files=files) == 42
```

项目运行中，组件字段可以保存 Agent；Registry provider/tool 仍接收经边界处理的数据，不能获得实例、Evaluator 或活调用栈。对象语义见[对象与组件](OBJECTS.md)。

低层 `Evaluator` 直接接收的同步 Python 回调属于受信任宿主扩展，保留旧 Function/Native/Agent 活值兼容接口；旧函数闭包可间接引用实例。这一接口不构成隔离沙箱。新对象出现在传入数据树中时转换为描述；Framework/Registry、HTTP API 与异步效果工作线程继续使用 JSON 数据边界。

## 13. 装饰器与属性在嵌入环境运行

SDK 与 CLI 接受相同装饰器语法，不需要 Web 服务。原始 IR 保留 wrapper 和内部声明，编辑器与 Client 可以修改装饰器参数，运行快照仍保留修改前的完整模块源码。

```python
from chaos import Framework

source = '''
class Box {
  var stored:Int=40;
  @property
  fn value(self){self.stored}
  @value.setter
  fn value(self,x){self.stored=x;null}
}
let box=Box();box.value=42;box.value
'''
assert Framework.evaluate(source) == 42
```

扩展提供方不接收活描述符。若需向模型传递属性的计算结果，应在语言中显式读取并按需 await，再作为普通数据发送；不要把属性摘要当作已经运行的结果。

## 嵌入用户描述符程序

```python
from chaos import Framework

source = '''
class Answer { fn __get__(self, object, owner) -> Int { 42 } }
class Box { let answer=Answer() }
let box=Box();box.answer=40;
let cached=box.answer;delattr(box,"answer");[cached,box.answer]
'''
assert Framework.evaluate(source) == [40, 42]
```

用户协议在语言解释器中执行。注册 provider 接收的是经过既有 JSON 边界转换的数据；传入 owner Class 或实例只得到惰性描述，不能在宿主侧调用其用户方法。需要计算后的属性结果时应在语言中显式读取。

持久项目按文件保留语法版本；保存改动文件时升级该文件，其它模块保持原义。低层 `ProgramBundle(source, files, entry, language_versions={"main.chaos":1})` 可以显式读取旧源码；默认是当前版本 2。已有 SDK 方法和参数保持可用。旧 IR 可通过 format_program 打印为显式兼容访问；不要仅因源码文本相同就忽略运行快照中的 sourceVersions。

## 0.18 目标、计划与技能

[目标、计划与可执行技能](AGENT_MODES.md)说明 Plan／Goal／Skill 的语言接口、持久化与恢复、真实工具录制、浏览器会话和 GUI／SDK 共同执行语义，以及当前实现边界。

## 0.19 模式库编写

Plan、Goal、Skill 现在支持统一验证、结构表单编辑、CAS 修订保存、固定版本加载和普通语言模块导出。界面保留与外部更新冲突的草稿；录制回执与新编写定义保持独立。详见[模式库编写与版本一致性](MODE_AUTHORING.md)。

## 智能体定义验证（0.20）

`Framework`／`Client.validate_agent(definition, *, name="saved_agent")` 返回规范定义、`definitionHash` 和普通模块 `source`；`agent_schema()` 返回 config 的直接 JSON Schema。纯验证不保存项目、不执行模型，公开配置不接受凭据或私有 backend。程序仍通过现有 `edit_program`／项目 CAS 保存源码。完整示例见[智能体定义值](AGENT_VALUES.md)。

## Team 定义（0.21）

Framework／Client 的 `validate_team(definition,*,name="saved_team")` 返回定义、哈希与普通模块源码，`team_schema()` 返回整个 Team 定义的 JSON Schema。操作无 provider 效果、不创建独立 Team 仓库；模块保存复用现有项目 CAS。见[团队指南](TEAMS.md)。

## 原生程序候选与采用（0.22）

Framework 和 Client 提供 `validate_program`、`program_schema`、`program_snapshot`、`adopt_program`。采用须显式 `program.adopt` 项目授权、版本/哈希基准和实际纯测试，与语言 `program_adopt` 使用同一宿主操作。现有源码/IR 编辑 API 不变；具体签名见 [Program 文档](SELF_PROGRAMMING.md)。

## 持久 Process

Framework 与 Client 共享 `validate_process(definition,*,name="saved_process")`、`process_schema()`、`create_process(project_id,version,key,definition,*,state=None)`、`processes(project_id,*,after="",limit=100)`、`get_process(project_id,key)`、`send_process(project_id,key,value,*,message_id=None)`、`control_process(project_id,key,revision,action)`、`configure_process(project_id,key,revision,definition)` 和 `reconcile_process(project_id,key,revision,state,*,evidence="",resume=False)`。生命周期由 Framework/服务持有，消息回执不代表完成；定义和恢复说明见[Process](PROCESSES.md)。

## 持久 AgentSession

Framework／Client 同名方法：`session_schema()`、`open_agent_session(project_id,version,key,agent,context_limit=100000)`、`agent_sessions(project_id,after="",limit=100)`、`agent_session(project_id,key)`、`agent_session_history(project_id,key,after=0,limit=100)`、`start_session_turn(project_id,version,key,revision,value,message_id=None)`、`fork_agent_session(project_id,key,revision,new_key,agent=None)`、`compact_agent_session(project_id,key,revision,keep_turns)`、`close_agent_session(project_id,key,revision)`、`reconcile_agent_session(project_id,key,revision,evidence)`。可选参数按实际 Python 签名为 keyword-only。send 返回标准 RunHandle；历史属于同一项目 Store。实例与事务边界见[会话指南](SESSIONS.md)。


## 环境证据接口

Framework 与 Client 提供 computer_schema、validate_computer_action、computer_sessions(project_id,after=0,limit=50)、computer_session(project_id,key)、computer_history、computer_record 和 computer_image；行动继续通过普通源码 Run 执行。字段、分页和 CLI 见[Computer](COMPUTERS.md)。

## 不可变程序历史

Framework／Client 的 `program_history(project_id,*,after=0,limit=50)` 返回 `{items,nextCursor,currentVersion,firstAvailableVersion}`；`program_revision(project_id,version)` 返回 `{definition,origin,metadata}`。元数据不包含源码，版本读取包含完整模块和方言。恢复继续调用现有 `adopt_program`，不新增跳过测试的 rollback 接口。精确格式与旧安装覆盖起点见[程序历史](PROGRAM_HISTORY.md)。

## 0.27 代码阅读接口

`analyze` 新增 `signatureHelp`，其值为 `null` 或 `{from,to,label,parameters,activeParameter,activeArgument}`。位置为 UTF-16 半开范围；参数项为 `{name,type,mode,optional,label}`。类型及签名来自静态可见的真实声明和可调用值元数据，分析不执行用户代码。动态参数展开后无法确定目标时 `activeParameter` 为 `null`。`language_versions` 是逐文件 1/2 方言映射，HTTP 名称为 `languageVersions`。

格式化可选 `line_width` 为 40–240、`indent_size` 为 1–8，严格整数且不接受布尔值。HTTP 字段分别为 `lineWidth`、`indentSize`、`languageVersion`；旧请求和返回字符串接口保持可用。CLI `fmt` 和 IDE 共用同一实现，保留注释与字面字符串、校验 AST 一致并拒绝不完整源码；长原子文本允许超出目标宽度。格式化是纯文本变换，不改变项目版本；调用者应在确认当前草稿仍与请求一致后再应用返回文本。

## 0.28 资源作用域

Framework.evaluate、项目执行、Client、CLI 与 IDE 共用 with/async with 语义，现有参数与接口地址保持不变。普通类钩子的 Agent／工具调用沿用项目能力和共享预算。临时 Computer 异步作用域退出调用关闭效果，持久 Computer 退出归还租约；硬取消跳过用户钩子，宿主仍排空效果并释放本轮资源。持久 AgentSession 仍需显式关闭。完整合同见[资源作用域](CONTEXTS.md)。

## 并发任务的收束

源码中的 gather、TaskGroup 与 SDK/HTTP 项目运行使用同一个调度器；async with 的组体和已接纳智能体任务沿用现有权限与预算。普通失败返回外层 catch 前，会等待批次已开始的宿主效果退出；后到的资源限制、运行取消或宿主中断不会被普通失败覆盖。合作式宿主回调仍须 checkpoint；运行时无法强杀任意 Python 线程。task_info 返回有界数据描述，不是可恢复的活 Task。见[并发任务](TASKS.md)。

低层 Python 宿主 `effect_handler(kind, payload, cancel_event)` 的既有合作取消方式保持有效：宿主确认传入的取消事件已设置后，可继续抛出 `TaskCancelled(message)`，无需填写运行时私有字段。调度器只在消费该任务的宿主结果时，把这一精确异常类型归属于已请求的局部取消；未收到自身取消请求的裸 TaskCancelled 仍按硬控制传播。ResourceLimit、其他 ExecutionControl 类型及宿主中断不会采用这项兼容归因。显式取消正在等待的任务仍中止等待者；取消未等待的无关任务不会污染正常结果。

## 同一语言的 Channel

SDK 与 HTTP 不另建消息队列；项目源码直接使用 Channel 和原有 TaskGroup／async for。消息可以在当前解释器中保存合规函数或智能体身份，SDK 结果与工具输入只得到不可还原的描述。不能把描述作为下一轮活句柄；持久消息仍通过 Process 协议提交。见[通道规范](CHANNELS.md)。

### 0.32 原生等待

Framework.evaluate／项目运行可执行 `await sleep(0)` 或 `await task_wait(jobs,until="all")`。返回分区中的 Task 仅在当前语言执行周期内存活，宿主输出是不可还原描述；不能把 JSON 再提交来恢复等待。TaskInfo 的新增 timing 为可空字段，原有 API 参数不变。详情见[计时与等待](WAITS.md)。

### 0.33 原子选择

Framework.evaluate、项目运行、CLI 和 HTTP 执行同一 send_case／receive_case／channel_select 语言代码，无新收发服务。ChannelCase 的 JSON 和宿主结果是安全描述，不携带可重建的权限或发送正文。原有接口不变；TaskInfo 新增可空 selection，见[原子选择](SELECT.md)。

### 可迭代值与调用组合

Framework.evaluate、已保存项目、Client 和 CLI 执行相同的星号迭代展开与高阶消费；同步游标只活在当前解释器执行周期，不能把宿主 Python 迭代器或 JSON 句柄描述传入以获得权限。公开 API/SDK 参数形状保持不变。参见[函数组合](FUNCTION_COMPOSITION.md)。


## 符号引用与重命名

`Framework.references`／`Client.references` 查询提供的完整包，`.rename` 返回编辑与完整候选，不直接提交项目。它们使用 `file/files/entry/position/language_versions`，以及 `include_declaration` 或 `new_name`。保存继续使用原有项目 CAS；完整返回结构与边界见[符号重构](SYMBOL_REFACTORING.md)。

## 词法取消保护

0.49 直接通过 Framework.evaluate、项目运行、CLI 和 HTTP 的既有源码接口执行 `with cancel_shield() as protection { ... }`；实际进入须位于活动 Task 中。没有独立的屏蔽 HTTP 开关。句柄只属于当前解释器和周期，JSON 描述不能恢复权限或活动范围。TaskInfo 新增 cancellationDeferred／shieldDepth，原字段和接口参数保持兼容；见[取消语义](CANCELLATION.md)。

## 增量编译报告

`compile_program(source, files=None, entry="main.chaos", language_versions=...)` 和 `Framework.compile(...)` 返回同源 `chaos.compilation/1` 报告；`Client.compile(...)` 调用同名 HTTP 能力。底层 SDK 可传 `compiler_cache=CompilationCache(...)` 或 `None`，缓存不会保留执行环境。`chaos compile FILE --root DIR` 输出同一报告；普通 CLI 进程退出后该源码分析缓存释放。完整字段与预算边界见[编译指南](COMPILER.md)。

### 宿主私有原生对象缓存

Flow 内部使用版本化的 rooted-host-events/2 调用 ABI，因此旧 PC／flag ABI 的物理对象不会误命中新制品。此变化不增加语言／HTTP 字段，不改变 `compile_info` 的12字段、`compile_flow_info` 的11字段或底层旧 `resume(pc,flags)` 二元组。native／portable 事件都通过当前调用的域、epoch、类型和代际 token 校验，不能从 JSON 或 SDK 参数取得活槽值；它不是公共对象指针／内存管理 API。

可信 Python 宿主可显式配置 `NativeObjectCache(directory, *, max_entries=32, max_object_bytes=4000000, max_total_bytes=16000000, lock_timeout=10.0)`，再传给 `NativeCodeCache(object_cache=...)`。当前显式持久存储要求 POSIX 私有目录和文件锁，父目录必须已经存在；Windows 仍可使用原默认内存缓存或解释器，不据此宣称持久存储已跨平台验收。普通默认路径仍只有进程内代码缓存。下例中的目录必须由宿主保护，不是项目源码或 HTTP 请求提供的对象上传位置。

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from chaos import Framework, NativeCodeCache, NativeObjectCache

with TemporaryDirectory() as directory:
    objects = NativeObjectCache(Path(directory) / "native")
    code = NativeCodeCache(object_cache=objects)
    result = Framework.evaluate(
        'let f=compile_function(fn(x:Int)->Int{x+2},"auto");f(40)',
        native_code_cache=code,
    )
    assert result == 42
    evidence = objects.info()  # aggregate entries/bytes and this instance's counters
    objects.close()  # release the store's file descriptors when no longer in use
```

同一 `native_code_cache` 参数可传给 `Evaluator`、`Framework` 和底层 `Engine`；`Framework.evaluate` 也透传它。参数只接受实际 `NativeCodeCache` 或 `None`，显式 `None` 禁用该入口的代码缓存。`Framework` 的持久运行和 Agent／Bot／Robot 子调用使用同一宿主配置，效果仍走原 Context 准入，不从缓存取得权限。HTTP `Client`、语言内建及 executable JSON 不提供缓存信任字段。

首次实际原生编译可保存可信构建器发射的对象字节；新进程命中时先认证精确生成 IR、符号、调用 ABI、后端版本、宿主目标和布局，再把对象链接到本次引擎，不重新发射机器码。仍重新进行原分析、守卫和编译检查点；没有后端时 `auto` 沿原规则回退，严格 native 目标沿原规则报错。对象不包含活 Function／Env／权限／输入／回调，ABI 不匹配、损坏、认证失败或超限不能进入 native linker。

目录和密钥权限、进程锁及原子发布保护同宿主私有缓存；内容认证不是任意第三方二进制的安全沙箱，也不抵抗已能读写宿主密钥的攻击者。`info()` 的磁盘条目／字节为当前保留量，命中等计数只属于本实例；不等于整个进程内存或执行次数。`code.clear()` 只释放进程内缓存引用，`objects.clear()` 显式清除磁盘条目，两者均不使活动函数的引擎失效。不再使用存储时调用 `objects.close()` 释放文件描述符；存储关闭不关闭已链接引擎。服务重启后可重链接匹配对象；这不是完整语言 AOT、跨目标发行、独立 GC 或性能加速保证。

## 0.65 生成式界面增量

详见 [生成式界面、报告与演示](GENERATIVE_UI.md)：UIArtifact 原生值、OpenUI 组件声明、真实流式生成、结构化需求验证、页面／报告与离线 HTML 演示。此增量不表示完整 Python／Lisp 能力已完成。

## 0.71 语言原生恢复

[Condition 与 handler/restart](CONDITIONS.md)由 Framework、SDK、HTTP、CLI 和 AgentTool 的共同解释器执行。宿主只接收纯情况／出口描述，不能恢复活句柄或从模型线程跳转语言调用栈。现有公共接口、JSON 与 IR 版本保持不变。


## 在宿主工程中复用数据边界与扩展方法

安装或按上文配置便携包导入根目录后，SDK 直接运行包内的标准模块；它不需要 Web 服务或模型凭据。

```python
from chaos import Framework

result = Framework.evaluate(
    'import "std/contracts" as C; import "std/generic" as G; import "std/iter" as I;'
    'let Request=C.contract_recursive({Node:C.contract_record({value:"Int",children:C.contract_list(C.contract_ref("Node"))})},"Node");'
    'let base=G.generic_method(G.generic_function("solve"),["Any"],fn(request:Request)->Int{request.children[0].value});'
    'let solve=G.generic_method(base,["Record"],fn(next,request:Request)->Int{next()+2},with_next=true);'
    'fold(fn(total,value){total+value},0,I.take(I.map(solve,[{value:0,children:[{value:40,children:[]}]}]),1))'
)
assert result == 42
```

示例通过 std/iter 按需映射一次 solve，再由 fold 消费；标准库源码随包加载，未启动服务或模型。with_next=true 让 Record 方法复用 Any 方法，next 保留这次调用的实参与版本；方法仍是普通语言函数。闭合递归契约沿现有函数参数与返回值检查嵌套载荷；描述值在本次解释器内创建，不从 JSON 恢复，见[组合契约](CONTRACTS.md)；带注解的函数也可作为 AgentTool 的实现，无需启动模型即可验证数据边界。持久方法表、类型分派和通用键状态仍在同一语言运行时，见[泛型函数](GENERIC_FUNCTIONS.md)与[HashMap](HASH_MAPS.md)。HashMap 的外部输出是不可执行的描述，不会变成自动调用用户回调的 Python dict；需要数据时先显式提取条目并投影为纯值。宿主可将源码放入现有项目文件，经原 CLI／HTTP 或 IDE 编辑执行；不必新增应用端路由器。

## 注册 Robot 资源

[Robot](NATIVE_ROBOTS.md)使用公开 RobotSpec／Registry.register_robot、openbot_robot 和 load_robot_registry(path, registry=None)。Framework.capabilities()／Client.capabilities() 的 robots 是静态、复制的注册元数据，包含 profile 与声明通道，不调用工厂或暴露端口／资源标识。注册与 robot.control 项目授权独立；该权限 scope=language、callable=false，默认不授予。原 run、源代码／IR、事件及 API v1 保持同一入口。CLI run／serve 的 --robots 可与 --devices 一起提供可信配置。


## 活动运行指导与公开工作流

Framework、Client 和 RunHandle 共用同一指导队列：`app.guide(run_id, input, message_id=None)`、`client.guide(run_id, input, message_id=None)`、`run.guide(input, message_id=None)`。input 是有界复制的 JSON，null 也是合法消息；纯文本可显式传 `{text: "..."}`。回执严格为 `{runId,messageId,revision,status}`，status 是 queued 或 received。同 ID／同数据幂等；同 ID／不同数据冲突。

```python
receipt = run.guide({"text": "请先验证结果，再继续下一步"}, message_id="review-first")
assert receipt["status"] in ("queued", "received")
```

`guidance.queued` 与 `guidance.received` 是真实宿主事件；received 证明语言已消费该消息，不证明其要求已经执行。`bot.progress` 的 `{kind,data}` 来自显式 progress_emit，kind 为 progress／plan／result；调用方的 JSON 不能把它变成宿主生命周期或指导回执。读取仍使用原 events／watch 单调游标和脱敏。接收是合作式的，未调用 guidance_receive 的程序可保持消息排队；正在执行的效果不重放、不自动中断。队列只属于活动 run，不跨重启恢复活任务或把消息注入 AgentSession 的 system／assistant 角色。


原生 Bot 使用现有 create_project／start 的 source 或 graph 运行入口；不新增 Python Bot 包装类、单独会话或授权接口。把真实回调传入语言 bot，运行收到的指导经 RunHandle.guide／Framework.guide／Client.guide 进入同一队列。`bot.context.applied` 是控制器应用消息的实际宿主事件，`bot.action.admitted`／`bot.action.completed` 带 scopeId、decisionId、revision、actionId；completed 或 uncertain 是实际效果结果阶段，不等于目标已实现。详情和完整回调示例见[原生 Bot](NATIVE_BOTS.md)。


### 持久 Process 指导

`Framework.guide_process(project_id,key,value,message_id=None)` 与 `Client.guide_process` 持久追加指导；返回 `{key,runId,messageId,revision,status}`，status 为 queued／received／applied，均不是动作成功回执。普通 `send_process` 保留下一轮 input 用途，同一 ID 不可跨用途重复。活动 Process 的 `RunHandle.guide` 进入同一持久身份；跨重启重试请用 Process key。回调仍由每轮普通模块／export 重建，不保存活 Bot。详见[持久指导](PROCESSES.md#0109持久指导与每轮原生-bot)。

## Process 的连续事件游标

`events(project_id,process_key=key,after=0,limit=100)` 与 `watch_process(project_id,key,after=0,timeout=60)` 使用同一可信 Process 选择器；`run_id` 与 `process_key` 互斥。旧 `events`、`watch` 和 `RunHandle.watch` 默认接口与逐运行终态行为保留。Process 观察跨各轮 runId，以实际事件 `id` 重连和回放。

```python
from chaos import Client
client = Client("http://127.0.0.1:8891")
for event in client.watch_process("project", "worker", after=0, timeout=60):
    print(event["id"], event["processOrigin"], event["type"])
```

长期观察并不调度 Bot，也不自动重试任何外部效果。`guide_process(...,message_id=...)` 的消息身份和实际内容必须保留到回执确认；不确定传输重试同一身份，变更内容使用新身份。读取页报告 `historyStartEventId` 及 `legacyHistoryIncluded=false`，不推断升级前未验证归属的事件。

具名移动设备、Mobile MCP 配置与权限边界见[移动设备与跨设备函数](MOBILE_COMPUTERS.md)。


### 定向 Bot 指导

`app.guidance_scopes(run_id)`、`client.guidance_scopes(run_id)` 或 `run.guidance_scopes()` 查询真实活动作用域；对象含 runId、active 和 scopes。可定向的 scope 有 addressable=true 及闭合 target 记录。将该记录传给 `guide(run_id, input, message_id=None, *, target=None)`；RunHandle 使用 `guide(input, message_id=None, *, target=None)`。省略 target 保持旧回执和原队列语义，None 不发送到 HTTP 正文。不同作用域的幂等身份独立；不要将迟到的回执解释为另一个目标的送达。

HTTP GET `/runs/{runId}/guidance` 查询列表，原 POST 路径只新增可选 `target:{kind:"botScope",scopeId:"bot-scope-…"}`。格式错误为 400；已关闭、外部运行、非私有根或 Process 的临时 botScope 目标为 409，不回退到原队列。声明角色的 Process 通过真实注册表提供 processRole 目标；省略 target 仍使用持久根入口。


长期角色指导：Process 定义的可选 roles 与 bot(...,role=...) 通过真实回合租约绑定。`process_guidance(project_id,key)` 读取持久角色列表；把其中的完整 target 交给 `guide_process(...,message_id=...,target=...)`。同一 target 和 messageId 重试保持原回执 runId，当前运行的 guide 也可使用角色 target。省略 target 保持原行为。角色删除重建后的旧 ID 返回冲突，不退回根。完整语义、恢复和容量见[原生 Bot](NATIVE_BOTS.md#长期-process-的角色邮箱)。


## 版本化多智能体共享记忆

通过原SDK／HTTP／CLI源码运行入口执行同一语言函数，不另开绕过项目能力的写入路径：

```python
from chaos import Framework, Settings
with Framework(settings=Settings(":memory:")) as app:
    app.create_project("memory", source='import "std/memory" as M; let c=M.read("answer"); M.compare_exchange("answer",c.revision,42)',
                       capabilities=["memory.read", "memory.compare_exchange"])
    receipt=app.start("memory",1,None,mode="source").result()
    assert receipt["swapped"] and receipt["value"]==42
```

`Client.start` 使用同一源码和权限合同。`revision:0`同样可能指升级前已有数据，需检查exists。旧get／put返回不变，put也使旧版本失效。冲突只返回当前快照，应用决定重新计算或合并；不会自动调用用户更新函数。提交后的任务失败不撤销记忆写入，Process恢复继续遵守原效果协调。完整字段及边界见[标准库](STDLIB.md#stdmemory版本化共享记忆)。


## 不可变词法整数优化

默认同一求值器会保留名称读取、函数调用、调试和效果边界，只在原运算处按实际类型和值守卫复用已知整数结果。编译报告的既有pass名 `literal-operation-specialization` 保留，算法身份为 `guarded-total-int/2`；候选数不是运行命中数。低层SDK可比较原参照执行：

```python
from chaos.language import Evaluator
source='let k=7; let alias=k; fn work(value:Int){value+(alias*6)}; [work(0),work(1)]'
evaluator=Evaluator()
assert evaluator.run(source)==[42,43]
assert evaluator.optimization_info()["appliedOperations"]==2
assert Evaluator(optimize_literals=False).run(source)==[42,43]
```

Compiler／Client.compile／CLI compile、Program与AgentTool的普通函数共同使用该计划；不缓存Agent或工具结果，不改变调用次数和顺序。变量Cell、用户协议及未知绑定仍走原路径。完整边界见[编译指南](COMPILER.md)。

### 精确 Process 消息结果

`app.get_process_message(project_id,key,message_id)` 与 HTTP Client 的同名方法只读指定普通邮箱消息，返回 `{key,messageId,mailboxStatus,linkStatus,run,reconciliation}`。run 关联原始运行及其完整 ProcessStep 输出；与 get_process(...).output 的最新检查点含义不同。queued／claimed／consumed 是邮箱状态，失败或已协调消息不能因此被称为成功。没有可信历史关联时 linkStatus=unavailable；损坏关联抛 Conflict，指导身份或消息不存在抛 NotFound。读取不发送消息、不重放轮次，也不启动执行器。

HTTP 为 `GET /api/v1/projects/{projectId}/processes/{key}/messages/{messageId}`，CLI 为 `chaos process message PROJECT KEY --message-id ID`。语言通过 `await process_message(key,message_id)` 组合这一读操作；同项目普通函数、AgentTool 和 Bot 可复用它，原有权限、录制和取消规则不变。

## 可验证的便携编译产物

`compile_artifact(source, files=None, entry="main.chaos", *, language_versions=None)` 返回 `chaos.compiled-artifact/1` JSON 数据；`load_artifact(data)` 通过同一实现返回新的原 `ProgramBundle`。产物保存闭合源码、方言、编译器／标准模块／依赖身份及可重定位的便携流模板，不保存 Env、Cell、Function、Task、能力、运行计数、PC 或原生地址。它包含源码，应按源码的保密要求保存。

装载会重新解析、链接、展开宏并确定性降低，逐项验证保存的模板、效果与节点路径；自行重算摘要不能使被修改的 CFG 合法。加载不是前端绕过，也不是 AOT 或执行授权。模块初始化、默认值及工具等运行期效果只在原执行路径实际到达时发生，重复装载和不同求值器保持独立环境。

模板仅在源码明确调用原 `compile_function(..., target="auto-flow" 或 "native-flow")` 且函数匹配时由原编译入口采用；函数形成不会自动编译。解释目标、未匹配的动态函数与不受支持部分保留原行为；实际 LLVM 准入仍取决于宿主可选依赖。旧 `chaos.compilation/1` 报告及 `compile_program`／`Framework.compile`／原 `chaos compile` 不变，报告不是新的运行输入。

SDK 导出同名两个函数；`Framework.compile_artifact`／`Framework.load_artifact` 使用同一实现，`Framework.validate_artifact` 只返回原包摘要。显式执行可以使用 `Framework.evaluate(load_artifact(data), bindings={"input": 40})`，仍是原纯求值合同，不新增工具、模型或设备权限。

命令行 `chaos artifact build FILE --root DIR` 向标准输出写 JSON；`chaos artifact validate artifact.json` 重新验证并输出包摘要；`chaos artifact run artifact.json --input-json 40` 才显式验证并纯求值。JSON 文件参数也接受 `-` 读取标准输入；命令不改写输入文件，不隐式加载宿主凭据或运行用户项目。

只读 HTTP 提供 `POST /api/v1/language/compile-artifact`（原 source／files／entry／languageVersions 请求）和 `POST /api/v1/language/validate-artifact`（仅 artifact 字段）；`Client.compile_artifact`／`Client.validate_artifact` 对应这两个入口。没有 HTTP 执行产物接口。HTTP 沿用原请求 4 MB／300000 数据值与响应 8 MB 限额；本地 SDK／CLI 的核心产物上限为 canonical JSON 8 MB／400000 值／256 层、512 模板及32768总指令，单模板仍受原1024指令等配额约束。CLI 输入文件另限原始 UTF-8 JSON 8 MB，带额外空白的文件也计入该输入限制。较大的本地合法产物不保证能通过更小的 HTTP 请求边界。

本节描述候选接口，不预告测试或安装通过，不声称加载提速、跨目标原生 ABI、可保存 continuation、独立 GC、完整 Python／Lisp 或物理机器人能力已经完成。AgentTool／Team／Bot 与 Robot 软件动作继续使用原运行时、权限、取消和不重放合同。
