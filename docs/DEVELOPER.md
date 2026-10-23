# 开发者指南

Chaos 可以作为独立语言使用，也可以通过公共 Python SDK 嵌入应用，或作为本地服务由 HTTP Client 调用。语言语义、宿主扩展与部署控制面分别负责各自边界；优先使用公开接口，避免直接依赖引擎内部字段。

## 选择开发路径

- 编写 Chaos 程序：使用[项目目录](GETTING_STARTED.md)，不需要修改解释器仓库。
- 将语言嵌入 Python：先完成下方“嵌入最小程序”，再注册工具和 provider；完整接口见[SDK](SDK.md)。
- 修改运行时：创建下方源码开发环境，测试同一解释器、CLI、服务和打包路径。

目录项目通过根目录的 `chaos.py` 调用原运行时。它与宿主 Python 中 `vendor/chaos` 的嵌入方式不同；不要把根启动脚本当作 SDK 模块，也不要复制另一份求值器实现目录 IDE。配置来源与公开环境变量示例见[模型配置](MODEL_CONFIGURATION.md)。

| 工程入口 | 创建或使用的内容 |
| --- | --- |
| 在解压后的 project 包中执行 `python3 chaos.py install TARGET` | 向目标工程安装干净运行环境及三个模板，不复制分发目录的配置、源码或历史；已有工程文件不冲突即可保留 |
| `python -m chaos project init TARGET` | 只创建 `chaos.py`、`chaos.project.json`、`main.chaos` 三个模板，依赖已有 Chaos 安装；不会复制内置运行时或安装依赖 |
| 在目标工程执行 `python3 chaos.py run`／`python3 chaos.py` | 从稳定启动器选择本工程运行时，执行源码或进入目录 IDE |
| 在目标工程执行 `python3 chaos.py replace NEW.zip` | 校验并自检完整 project／portable ZIP，选择新环境，保留用户源码与数据 |

`TARGET` 和 `NEW.zip` 换成实际路径；含空格时加引号。Windows 使用 `py -3`。旧项目没有 replace 命令时，从解压后的新版 project 包执行 install 到旧工程，可迁移与原包一致的官方启动器。已有入口若声明并实现当前环境协议，则保持入口字节不变；不支持协议的定制入口不会被覆盖。具体操作和恢复见[安装](INSTALLATION.md)与[更新](UPDATES.md)。

初始环境为 `.chaos/runtime` 与 `.chaos/deps`；替换后由 `.chaos/environment.json` 选择 `.chaos/environments/env-…/` 下成对的 `runtime` 和 `deps`。通过 `python3 chaos.py status` 查看活动路径，不在宿主程序里固定引用初始运行目录。用户源码、`.chaos/settings.json`、`.chaos/project.sqlite3`、`.chaos/cli.sqlite3` 和任务数据仍留在工程原位置，不进入版本环境。

根启动器使用显式的 `chaos.environment/1` 协议选择环境，启动前确认路径并持有使用锁；替换检查相容协议、Python 要求、完整包与候选语言运行，成功后才原子切换指针。依赖按需求、解释器和平台指纹匹配，不能把某个版本的 deps 目录当成跨系统通用依赖。回退恢复上一运行时及其依赖，不撤销源码、配置或业务效果。保留的基础目录用于兼容，不意味着它永远是活动运行时。

## 统一运行包与资源边界

`setup.py` 将现有 web/docs/examples/vendor 资源放进同一 `chaos/` 包；`chaos.release.validate_wheel` 校验源码与 wheel 的逐字节一致性。`chaos.portable` 从该 wheel 派生完整可复制目录，不生成第二份解释器、SDK 或命令解析器。完整 wheel 的 `.dist-info/METADATA`、许可证及 `RECORD` 保持原始字节；依赖条件由元数据保留，遵循 [PyPA 安装记录规范](https://packaging.python.org/en/latest/specifications/recording-installed-packages/)。

`chaos.resources` 是统一资源适配器：先选择包内资源目录；仅当包父目录有匹配的 Chaos 发行元数据与构建文件时，开发检出才使用其同级资源。普通宿主项目同名目录不能成为隐式后备。显式 Documentation 资源参数仍保留。包内 stdlib 和 GPU worker 已按自身位置加载；std/iter 的真实 `.chaos` 文件由同一资源路径读入 ProgramBundle，发布校验逐字节验证该文件，不用宿主迭代器重写语义。代码解释器通过同一导入根目录提供当前运行包。

包提取先验证有界 ZIP 路径／大小、重复和大小写别名、链接、RECORD 哈希、版本及清单，准备完整临时内容，再独占创建新目标，完成清单最后写入。并发调用不能叠加或替换已有目录；提取期间不承诺整个目录原子可见。宿主应在成功返回或校验完成后导入，升级选择新的完整版本目录并重启。数据库和配置始终属于宿主应用，不作为包资源导出。

## 创建开发环境

在源码根目录执行：

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python -m unittest discover -s tests -v
python -m chaos docs check
```

测试使用确定性本地程序、假模型适配器及临时数据库；真实模型请求与付费图像调用需要另外配置并单独验证。浏览器集成需要 [可选安装](INSTALLATION.md)，不是每个语言测试的前提。

## 解释器、IR 与打包

| 阶段 | 组件与结果 |
|---|---|
| 词法与语法 | `chaos/language.py` 解析 `.chaos` 程序，生成带源码位置的内部语法结构 |
| 模块装配 | `chaos/modules.py` 验证程序包、相对导入、导出与标准模块 |
| 增量编译 | `compilation_cache.py` 有界缓存原始 AST，`compiler.py` 生成依赖身份和保守分析报告 |
| 程序结构 | `chaos/program.py` 提供 JSON IR、格式化、结构补丁及工作流投影 |
| 求值 | `Evaluator` 在受限步骤、时间和数据大小内解释执行 |
| 项目运行 | `runtime.py` 把源程序、工具、智能体与图调度纳入快照和共享预算 |
| Python 包构建 | setuptools 将运行时、网页、文档、示例与 vendor 打进 wheel |

组合／递归契约复用 `chaos/contracts.py` 的不可变描述代数、纯预算检查及已有注解边界。开放模板可以跨普通语言函数传递，闭合组才可检查载荷；不要在宿主按诊断类名重建名义身份，也不要由 IDE 执行契约函数猜字段。便携包运行同一模块，不需要模型后端，见[契约指南](CONTRACTS.md)。

`chaos check` 不执行程序，`chaos ir` 输出可编辑 JSON 结构，`chaos run` 解释执行。`chaos compile` 已提供增量前端的依赖与结构效果报告，详见[编译指南](COMPILER.md)。0.60 另有可选运行期标量区域 JIT，见[原生函数](NATIVE_FUNCTIONS.md)；IR 导出、静态编译报告与 wheel 打包均不代表机器码发布。

## 嵌入最小程序

纯计算可直接求值：

```python
from chaos import Framework

assert Framework.evaluate("fn square(x) { x * x }; square(7)") == 49
```

需要项目版本、持久记忆和运行证据时创建 Framework：

```python
from chaos import Framework

with Framework() as app:
    project = app.create_project("double", source="input * 2")
    run = app.start(project["id"], project["version"], 21, mode="source")
    assert run.result(timeout=5) == 42
    assert run.get()["snapshot"]["source"] == "input * 2"
```

`Framework()` 使用内存数据库；传入路径可持久化。Framework 必须关闭，建议使用 `with`。默认嵌入配置不读取用户磁盘密钥；宿主需要部署配置时显式传入 `Settings()`，例如 `Framework(settings=Settings())`。完整生命周期、分页和取消契约见 [SDK](SDK.md)。

## 把执行进度接入自己的界面

先复用运行与事件接口，把程序结果、公开进度和用户输入分开处理。下面是完整的无模型宿主例子：

```python
from chaos import Framework, Settings

source = """
await progress_emit("plan", {summary: "计算输入的平方"});
let answer = input * input;
await progress_emit("progress", {summary: "计算完成", answer: answer});
answer
"""
with Framework(settings=Settings(":memory:")) as app:
    project = app.create_project("square-progress", source=source)
    run = app.start(project["id"], project["version"], 7, mode="source")
    for event in run.watch(timeout=5):
        if event["type"] == "bot.progress":
            print(event["payload"]["data"])
    assert run.result(timeout=5) == 49
```

`progress_emit` 和 `guidance_receive` 需要真实 Run 提供指导宿主；纯表达式求值 `Framework.evaluate` 不提供这个宿主。进度仅接受 `plan`、`progress`、`result` 三种公开类别和有界 JSON。进度事件里的 `result` 类别不终结运行，最终成功仍通过 `run.result()` 或实际终态判定。

外部服务可把 `Framework` 换成连接同一服务的 `Client` 并使用其公开运行方法；`RunHandle.watch`／`Client.watch` 通过持久事件分页观察，不是 HTTP SSE 解析器。浏览器或其他 SSE 客户端使用[实时订阅接口](REALTIME.md#从其他程序订阅实时事件)。运行、后台任务和助手有各自的事件范围，选择器和游标不能混用；同一状态视图应以服务返回的身份、版本和有序事件为依据。

用户追加信息通过 `run.guide({"text": "…"}, message_id="…")` 进入当前运行；语言在 `await guidance_receive(...)` 边界接收。收到消息只证明投递，不自动执行其中的文本。完整可运行示例与可核对结果见[交互教程](REALTIME.md#从运行到追加指导)。编程助手提案的生成、审阅和应用使用既有助手接口，另见[SDK 的 IDE 接口](SDK.md#9-ide-与程序包操作)。

自建 UI 应保留编辑草稿，并将它与已保存版本及执行快照分开。读取失败后重试观察；不要自动重发生成任务、工具调用或提案应用。消费完终态尾事件再结束订阅，任务快照先返回终态不意味着全部事件已显示。停止观察不会自动取消服务任务；关闭拥有运行的 Framework 会结束其生命周期。

维护工作台时，`web/realtime.js` 集中处理确认游标、重连、去重与页面缓存恢复，`web/sync.js`、`web/activity.js`、`web/ide.js` 分别消费对应事件。保持这些消费者的项目／任务身份检查；它们是内部实现位置，不是宿主应用应依赖的公共 Python API。

## 注册工具和提供方

`Registry` 是应用明确注册工具与智能体行动协议的入口。下面是可直接执行的完整确定性示例，无网络和模型密钥依赖：

```python
from chaos import Final, Framework, Registry, ToolCall, Workflow

registry = Registry()

@registry.tool("app.double", input_type="Int", output_type="Int", effect="pure")
def double(value, context):
    context.checkpoint()
    return value * 2

@registry.provider("app.planner")
def planner(request, context):
    context.checkpoint()
    if request.step == 1:
        return ToolCall("app.double", request.input)
    return Final({"answer": request.history[-1]["output"]})

flow = (Workflow().input()
        .agent("solver", "将输入翻倍", provider="app.planner", tools=["app.double"])
        .output()
        .connect("input", "solver")
        .connect("solver", "output"))

with Framework(registry=registry) as app:
    project = app.create_project("example", graph=flow, capabilities=["app.double"])
    run = app.start(project["id"], project["version"], 21)
    assert run.result(timeout=5) == {"answer": 42}
```

工具需要同时获得项目 `capabilities` 与调用主体 `tools` 授权。提供方每步返回 `ToolCall` 或 `Final`，由运行时检查和执行动作；模型不能通过提供方返回的动作绕过工具授权。注册表在 Framework 创建时快照；不要依赖后续注册改变已创建实例。

回调运行在宿主 Python 中，属于可信扩展。定期调用 `context.checkpoint()` 协作取消和检查预算；不合作的宿主函数不能被线程强制终止。JSON 边界限制与 `Final` 的空值语义见 [SDK](SDK.md)。更完整的多智能体应用可运行：

```sh
python -m examples.support_assistant
```

此命令在源码根目录执行，两个提供方都是确定性示例；按自己的协议实现适配器，或使用内置 `model` 提供方连接部署配置。

## 插件与模型后端

`Registry` 扩展语言运行时的工具和提供方；`PluginHost`/`PluginSpec` 管理宿主服务依赖、启动与清理。二者目的不同。控制面的 `code/browser/image/native/codex` 是预注册服务，网页配置只启停已知 ID，不从 HTTP 文本导入 Python 模块。

公开插件接口可在可信 Python 宿主中独立使用：

```python
from chaos import PluginHost, PluginSpec

closed = []

def install(ctx):
    ctx.provide("app.greeter", lambda name: "Hello, " + name)
    ctx.effect(lambda: closed.append(True))

host = PluginHost()
try:
    host.add(PluginSpec("greeting", install, version="1"))
    assert host.require("app.greeter")("Chaos") == "Hello, Chaos"
finally:
    host.close()
assert closed == [True]
```

声明 `requires=("service.name",)` 的插件在依赖可用后激活；依赖消失或替换时会清理并协调消费者。插件生命周期是 Python 子集，不兼容上游 JavaScript 插件 ABI。内置控制面扩展点、来源和 native/Codex 的工具差异见 [Harness](HARNESS.md)。

## HTTP 服务集成

独立服务使用 `Client("http://127.0.0.1:8765")`；同进程已有 Framework 时可将其借给 `chaos.server.make_server(framework=app)`，避免重复打开数据库。借用模式由调用方关闭 Framework；独立服务自行持有生命周期。示例见 [SDK](SDK.md)。

[HTTP API](HTTP_API.md) 包含版本化端点与机器可读 OpenAPI 入口。新应用使用 `/api/v1`，旧 `/api` 为原网页保留兼容形状。写操作带项目版本，处理 409 冲突时重新读取并合并；不要无条件重试有副作用的 POST。观察事件使用游标，查询页有上限；等待超时不会取消运行。

服务针对可信单用户主机，项目隔离不是多租户身份认证。注册工具运行权限、网络边界和部署方案见 [安装与部署](INSTALLATION.md)。

## 源码地图与验证

| 路径 | 责任 |
|---|---|
| `chaos/language.py`、`modules.py`、`program.py`、`session.py` | 语言、模块、结构编辑、交互会话 |
| `chaos/framework.py`、`client.py`、`extensions.py`、`workflow.py` | 公共 SDK、HTTP SDK、扩展协议、图构造 |
| `chaos/runtime.py`、`graph.py`、`store.py` | 调度、结构权限、项目事务与持久化 |
| `chaos/settings.py`、`control.py`、`background.py`、`harness.py` | 配置、任务队列、模型与后端 |
| `chaos/plugins.py`、`execution.py` | 服务生命周期、代码和浏览器工具 |
| `chaos/server.py`、`cli.py`、`workspace.py` | HTTP/SSE、CLI、文件同步 |
| `chaos/project.py`、`project_launcher.py`、`project_environment.py` | 目录项目、稳定入口、环境选择、本地安装／替换与回退 |
| `chaos/portable.py`、`resources.py` | 同源包生成、完整性校验与包内资源定位 |
| `chaos/updater.py`、`managed.py`、`release.py` | 签名更新、启动监督、发布 |
| `web/`、`docs/`、`examples/`、`tests/` | 无构建前端、单源文档、示例、回归测试 |

修改语言时验证语法、求值、模块、IR 格式化往返与错误定位；修改运行时或扩展时验证能力、取消和持久快照；修改 HTTP/同步时验证版本冲突和事件回放；修改发行资源时验证 wheel 在 checkout 外可运行。完整 `unittest` 套件是发布前的共同基线。

改变目录启动器或环境管理时，还需从真实上一版交付包出发验证“安装／迁移 → 执行 → 替换 → 回退 → 再升级”。在源码目录之外、带空格路径和不同工作目录测试，核对源码、设置与数据库哈希不变；验证运行中拒绝替换、损坏包与候选失败保留旧环境、依赖指纹不匹配时要求 setup。用固定版本测试夹具覆盖协议分支，不能以夹具代替真实旧包迁移。仅在实测系统上报告已验收。

## 构建与发布

本地普通 wheel 构建（运行时 Python 3.9+；构建使用 setuptools 77+，保留不同组件的许可证路径）：

```sh
python -m pip install "setuptools>=77" wheel
python -m pip wheel --no-deps . --wheel-dir dist
```

wheel 使用 Python 源码，不编译 `.chaos` 为机器码。需要可签名升级包时，使用 [发布流程](RELEASING.md) 的 `python -m chaos.release build`，不要仅把普通 wheel 复制到更新目录。发布者显式修改 `chaos/version.py`，同步行为文档与测试，验证 API、存储和安装器兼容性；不能靠修改兼容标记跳过真实迁移。

每个版本的文档与实现一起构建、测试和装包。已交付版本的内容保持不可变；即使只修正文档或学习示例，也使用新的补丁版本，不重新发布同版本但不同字节的 wheel／ZIP。维护入口、可执行示例登记、静态站导出和审阅规则见 [文档维护](MAINTAINING_DOCS.md)。


## IDE 集成契约

`ide_language.py` 提供不执行用户代码的分析与保留注释的格式化；UTF-16 编辑范围不能直接当成 Python 字符索引。`ide_files.py` 在既有项目版本之上转换模块包，使用解析后的 import 定位进行重命名，再通过 `save_project` 原子提交。`ide_agent.py` 使用项目绑定会话、原生 harness 与后台任务持久化候选修改；应用始终核对项目和基础版本，不解析回答文本来推断编辑操作。

编辑器资源在 `web/vendor`，包含锁定依赖、构建入口、构建脚本和上游许可证；最终安装包直接提供离线 bundle，不在用户启动时下载编辑器。`web/ide.js` 与原有 `sourceEditor` 草稿接口同步，通过已有保存、SSE 和冲突流程提交；不能增加绕过版本检查的第二套工作区。

新增 IDE 能力时，同步维护 [IDE 指南](IDE.md)、SDK 与 OpenAPI，覆盖 Unicode 范围、无效草稿、异步回复过期、重命名后的相对依赖、不同项目会话隔离，以及安装包中编辑器资源的可用性。

## 宏、挂起与调试的实现边界

`macros.py` 从已解析 AST 生成单独的 `expanded_trees`，原始源码树仍是 IR 与编辑器的权威表示。编译期不能获得运行时 Native/Agent 或宿主工具。`language.py` 的可挂起求值路径服务普通函数、生成器和异步函数；`suspension.py` 仅在一个 AST 线程上推进环境，宿主效果池接收复制数据。`debugger.py` 在解释器 checkpoint 暂停，HTTP/SDK/CLI 通过版本化运行和 pauseId 操作同一状态。

IDE 助手通过 `language.reference` 结构化工具读取随版本发布的语法与语义文档，再用 draft.validate/evaluate 验证提案。`ide_agent.LANGUAGE_REFERENCE_TOPICS` 是工具 schema 和实际分派共用的显式主题清单；新增语言指南时同步登记实际 catalog ID，并通过真实 harness 工具调用验证返回的版本和 Markdown。未登记主题不能作为路径读取文件。普通文字或代码围栏依旧不能直接改项目；这些规则不依赖对自然语言关键词的硬编码分类。


## 流协议和模块宏扩展

async_gen_fn 与 async_for 是实际解析器节点，结构表示、格式化和 IDE 分析须同时支持。生成器操作保存暂停点并向其送入值或异常；异步操作被包装为 Task，busy 状态只在任务执行时获得。扩展宿主工具仍只能接收数据和 Context，不直接修改解释器 Env 或在效果线程推进 AST。

宏的模块来源是展开树的私有元数据，原始树/IR 保留用户源码；克隆模板与 unquote 语法时不得丢失定义处/调用处的解析环境。`exports` 的运行期契约保持不变，编译期公开项单独放在 `macroExports`。应使用包含跨模块同名辅助宏的测试验证卫生，而不仅测试格式化输出。

## 扩展对象语义

类、实例和绑定方法继续使用语言解释器的调用入口、类型契约和调度器；禁止在宿主中直接调用语言方法来跳过上下文。AST 新增 class 和 set_member，旧 assign/index 形状及 IR schema 保留。宏卫生处理类体时必须保留公开成员标签，只重命名词法绑定。

对象与宿主效果之间使用惰性行为之外的纯数据描述，不能把活实例或绑定方法图交给效果线程。调试器也不能在渲染期间执行属性。更新语言时同时验证类型身份、C3、类默认值共享、可调用实例、宏和 GUI/SDK/CLI 一致性。见[对象规范](OBJECTS.md)。

低层 `Evaluator` 直接接收的同步 Python 回调属于受信任宿主扩展，保留旧 Function/Native/Agent 活值兼容接口；旧函数闭包可间接引用实例。这一接口不构成隔离沙箱。新对象出现在传入数据树中时转换为描述；Framework/Registry、HTTP API 与异步效果工作线程继续使用 JSON 数据边界。

## 装饰器与内置描述符实现

`decorated(decorators, declaration)` 是附加 AST/IR 节点；旧 def/class/agent 形状不变。宏遍历、模块导出、静态分析和结构编辑必须解读包装后的声明绑定，不能把装饰器当字符串预处理。运行时先求装饰器表达式，再构建原值、反向应用、发布最终绑定；函数自递归和类自身类型保留原值身份。

成员访问通过解释器的可挂起入口执行。getattr/setattr/hasattr 的内部访问请求仍在当前任务中处理，不能直接在 Python 中调用语言 getter。StaticMethod/ClassMethod/Property 仅为显式语言描述符；预览、vars/default_dir、序列化不得触发属性钩子。显式语言 dir 通过同一协作调度调用 __dir__；五属性钩子按真实类／元类原始 MRO 查找，default_* 保留描述符／存储保护。普通静态属性提示与原始协议元数据分离，见[属性协议](ATTRIBUTE_PROTOCOL.md)。未知装饰器的静态类型只能保守推导；内置名字被遮蔽后不可继续套用内置语义。

## 用户描述符扩展

用户协议复用原 class/call/member AST。对象层只做原始 MRO 查找、优先级与内部调用请求构造；解释器通过统一 `_s_invoke` 解析和执行协议。描述符自身的协议方法也可能使用描述符，解析必须有预算和深度边界，不能在宿主递归中失去计费。

建类通知处理最终自身成员的固定快照，完成成员后可访问 owner，但直到通知和类装饰器成功才发布外层绑定。保留非事务副作用边界。property 的 getter/setter/deleter 副本必须携带其它访问器与完整 previous 血缘。

`tests/test_descriptor_protocol.py` 覆盖协议矩阵，`tests/test_descriptor_integration.py` 覆盖真实注册提供方、取消、权限和多接口一致性。新增分析规则需同时测未知返回、可变成员、递归协议与遍历次数，避免类型误报或指数成本。

## 运算协议实现

解释器通过内部延迟运算请求把原生内建与语法运算统一接入可挂起执行链，避免在同步 Native 回调中另启解释器。对象层继续负责原始 C3 查找和描述符绑定；具体回退、返回验证及资源限制由语言运算调度负责。普通模式匹配保留纯结构比较，运行时 ==/!= 另走可挂起递归比较。源码/IR schema 不变，新增内建与协议名使用普通 name/call/def 节点。

## 用户迭代协议实现

独立迭代模块保存语言句柄与每周期适配缓存；延迟迭代请求把 Native API 接入同一个 generator-based 执行链。不要在语言 for/collect 消费路径中另启 AST 线程，也不要用宿主 StopIteration 代替用户 done/value 记录。

新增协议必须同时处理原始 MRO、描述符绑定、所有权、epoch、busy、正常关闭及不可捕获的控制信号。清理可能再次产生 Task，运行结束必须排空这些任务；已有主异常不能被清理错误覆盖。宏保持纯展开，IDE/debugger 只查看有界元数据。

## 索引协议和逐模块语法版本

新增 item 请求与属性／运算／迭代请求同走 Evaluator 调用入口。parser 必须将点属性、显式 legacy `.[key]` 和新条目 `[]` 分开；不要把 key 的字符串内容当作语法。Slice 是不可变标量组合值，JSON 描述不能恢复其名义类型。

ProgramBundle 的可选 language_versions 以文件为键；旧项目的源版本从持久 IR 推导，源码保存只升级改动模块，文件改名保留其语法版本。Store 保存版本映射与源码／IR 的 CAS 在同一事务中。程序结构编辑可以将历史节点打印为显式旧访问，因此不依赖可遮蔽的内建函数名。新增顶层 IR 元数据不进入 AST 语义哈希；运行快照保留该元数据。

## 0.18 目标、计划与技能

[目标、计划与可执行技能](AGENT_MODES.md)说明 Plan／Goal／Skill 的语言接口、持久化与恢复、真实工具录制、浏览器会话和 GUI／SDK 共同执行语义，以及当前实现边界。

## 0.19 模式库编写

Plan、Goal、Skill 现在支持统一验证、结构表单编辑、CAS 修订保存、固定版本加载和普通语言模块导出。界面保留与外部更新冲突的草稿；录制回执与新编写定义保持独立。详见[模式库编写与版本一致性](MODE_AUTHORING.md)。

## Agent 定义边界（0.20）

`chaos/agent_values.py` 统一公开配置 schema、规范定义验证、哈希、源码生成及 Evaluator 内建。Framework、HTTP 与结构编辑器复用它；Runtime 只为可公开验证的调用添加定义哈希，不改变旧声明和受信任 Python 回调的调用 ABI。回归覆盖 provider 产生候选、Plan 绑定、Goal 检查点重建、工具授权与取消；详见[智能体定义值](AGENT_VALUES.md)。

## 语言内生团队（0.21）

`team_values.py` 负责纯定义、所有权、成员函数和惰性 Team 请求。请求在已有 Evaluator／Task 内转为 Agent Effect；`_team` 是解释器产生的内部来源与缩权数据，Context 与现有 allowed 求交后分派，不能将用户 Record 中的同名字段当成授权。角色元数据随效果事件传播，不传 Env／闭包给工作线程。无新数据库、scheduler 或 provider backend。规范、上游固定源码与未实现边界见[团队指南](TEAMS.md)。

## 自编程执行链（0.22）

Program 由 `program_values` 管理不可变定义、所有权与反射；`program` 继续提供唯一 IR 编辑器。Program Task 通过新的 program 效果传递有界 JSON，Context 在独立解释器内继承总预算、取消与权限。省略 fixtures 的纯测试禁用效果；显式 fixtures 使用共享隔离求值器执行真实语言工具并验证协议。采用重跑测试后复用 Store 事务 CAS，并原子记录采用与 graph.saved。

不把 Env/闭包/Task 交给效果线程；ProgramBundle 编译检查点与宏展开共享预算。录制中拒绝该效果并禁止封存；父调试器不会混入子解释器同名 frame ID。公开测试与 [语言合同](SELF_PROGRAMMING.md)覆盖这些边界。

## 持久语言轮次

Process 的定义属于解释器值；状态／邮箱在现有 Store 中，调度生命周期属于 Engine。导出函数在一个完整 evaluator epoch 内初始化、调用、await 和 drain，ProgramBundle 原入口保留以校验 current_program 的来源。领取与 run 创建、状态提交与 run 终态分别原子；工具效果和立即执行的 program_adopt 不属于最终状态事务；直接返回 process_evolve 的候选采用则与 Process 检查点共同提交，见[演进协议](PROCESS_EVOLUTION.md)。修改时维护 lease、取消线性化、权限上限、暂停更新派发和 needs_reconciliation 的不重放不变量。测试矩阵与限制见[Process](PROCESSES.md)。

## Session 持久原生协议

AgentSession 的受控值／冷效果进入现有 Context/Engine；NativeHarness.run 与 run_session 共用模型循环。协议层用真实工具名记录公开消息，传输时生成稳定别名，Store 同库保存租约、工具批次前置日志、完整结果及幂等回执。每次调用独立提交，父 Process 不回滚已提交会话；测试须覆盖提交后父失败、同ID取回、真正工具后崩溃与恢复不重播。GUI/SDK/CLI使用同一契约，见[会话设计](SESSIONS.md)。


## 环境资源扩展边界

computer_values/computer_schema 定义受控值与闭合结构；computer_host/computer_store 管理 Run 租约、项目持久会话和观察行动日志；BrowserBroker 保证工作线程归属。ObservedInput 经私有身份桥转为 native canonical user parts，普通 JSON 不启用该路径。添加新后端须实现同一观察身份、权限、取消、回执和资源终结语义，不能另建 UI 执行引擎。详见[Computer](COMPUTERS.md)。

## 程序版本捕获

ProgramHistoryStore依附现有Store连接；create/save/patch/adopt在同一事务追加快照，失败整体回滚。Context的history/revision只读效果和程序执行效果分开，专用返回通道生成owned Program而非复活任意JSON对象。新增接口保持原API行为；覆盖测试包括迁移、CAS、文件方言、跨重启恢复和UI迟到响应。详见[程序历史](PROGRAM_HISTORY.md)。

## 语言服务和代码呈现

`ide_language.Analysis` 使用执行器同源词法与 Parser，仅读取静态元数据。`signatureHelp` 复用实际作用域和 `callable_info`，参数位置由词法分隔符确定，字符串／注释中的逗号不参与计数；动态展开保持未知。声明与引用应沿用同一个绑定的类别，函数／类解构叶子不能仅在引用处正确着色。已知封闭记录与未知记录形状须区分：只有完整已知的键集合才能诊断多余键，不能把部分成员提示当成完整类型证明。不要运行用户函数来补全类型。

`source_format.format_source` 为源码排版入口，`ide_language` 保留兼容委托；IDE、SDK、HTTP 和 CLI 共用该入口。它保留原子文本、比较格式化前后 AST 并检查幂等性测试。`program.format_program` 是从 IR 重建代码的独立公共接口，其注释不保留语义没有改变。

`code_presentation` 为文档构建转义后的 token span，`web/code.js` 用相同范围增强只读预览。在线和静态文档分别保留原始源码与格式化阅读副本，复制和导入继续取原文，行号不进入源码。阅读副本复用共享格式化器并重新分析，不能复用原文偏移或改变保存的模块包。静态文档预计算副本，离线切换无需 HTTP；动态预览的异步格式化和分析只可应用到仍匹配的内容。带调试当前行的视图保留原始行号，不提供排版切换。Chaos 围栏使用语言分析器，Python 围栏使用标准库 tokenizer，其他语言保留原文；语言标签不能根据内容猜测。新增静态资源必须同时纳入 HTTP、离线文档和发行物。颜色分类不应维护第二份 Chaos 关键词表。

检查代码呈现时分别覆盖可编辑源码、工作流函数体、候选／历史／生成源码预览和执行快照。快照分析必须使用该次运行保存的模块包与逐文件方言，不能借用当前项目的定义；JSON 配置、输入与运行结果仍是数据。浏览器回归应检查实际 token 颜色、悬停／键盘／触屏提示、原文复制、阅读副本与原文切换、格式化后的 AST 与保存重载结果，并验证切换节点、项目或编辑内容后迟到的分析／格式化响应失效。格式化自身不能触发执行或隐式应用提案。

## 0.28 上下文协议与资源作用域

`contexts.py` 通过现有特殊方法绑定、统一调用入口与 AST 调度器实现 with/async_with；没有独立解释器或权限通道。`modules.py` 中 std/context 以普通 Chaos 类实现关闭适配。解析器、宏卫生、program schema、IDE 分析和结构表单共享新节点。新增语法时同时验证正常／异常／非局部控制／流挂起、硬取消、native Computer 宿主关闭、完整旧 IR 和版本同步。见[协议规范](CONTEXTS.md)。

## 任务汇合的边界

Scheduler 与 task_groups 模块负责成员预检、唯一归属、真实 scope Task、独立组中断、完成通知、取消来源和完整排空；普通组失败不得复用外层 Task 的永久取消位。Context 的 TaskBudget 区分本任务取消与运行级硬控制。宿主错误必须在取消已请求后仍保留身份，不能一律替换为 TaskCancelled。任务元数据由同一 scheduler 生成，调试 HTTP 不读取活解释器。见[任务规范](TASKS.md)。

## 原生通信操作

channels 模块与 Scheduler 共用解释器线程，send／receive 以专用冷 Task 注册请求；消息配对／入缓冲时原子完成操作，提交后的 cancel 不能制造伪撤回。取消、失败和 epoch 收尾在终态通知之前移除等待项。Iteration 每次为 Channel 建立独立 AsyncIterator，仅拥有自己的接收操作；不得关闭共享通道或取消借用的外部 Task。载荷在创建时复制，逐层所有权检查必须在创建、启动及交付入口执行，宿主仅接收描述。调试与 IDE 读取真实元数据定义，历史 AST／IR 不因新增内建值改变。实现与测试边界见[通道规范](CHANNELS.md)。

SourceAnalysis 增加可选 referenceHints 数据，采用与 tokens 相同的 UTF-16 范围并来自已解析引用，不改变 symbols 的声明语义或原有必需字段。代码展示合并引用与声明提示，不逐词再次分析或执行用户代码。

### 原生等待与语义元数据

原生 sleep／task_wait 在同一调度器注册期限或 Task 观察关系，不调用模型或占用宿主效果线程。API／SDK 继续执行原语法，TaskInfo 新增可空 timing，不替换旧字段。Native 的可选 spec_id 指向不可变原生签名及值形状，分析器不从函数名称推断身份；已有宿主 Native 四个位置构造参数保持兼容。新增声明必须用测试比对实际 callable 参数和实际状态形状。完整契约见[等待规范](WAITS.md)。

## 0.33 原子选择协议

channel_select 模块提供 ChannelCase 和原生选择操作，channels 队列同时接纳普通操作与选择候选。配对先原子确定双方赢家并撤销败选，再交付与发布终态；队列维护不能执行用户 getter、函数或效果。可复用模板及各次捕获都计入共享资源限制；创建、启动、实际交付复验归属。TaskInfo.selection 是新增可空字段，原有调用方式和 AST 不变；共享 NativeSpec 同步提供类型、参数和完成值。详见[选择规范](SELECT.md)。

## 可迭代调用与高阶消费

调用星号展开与 apply/map/filter/fold/any/all 必须通过 iteration 的协作式 acquire/step 和原有 _s_invoke 执行，不能在宿主回调循环中重新驱动解释器。消费期间锁定借用游标，退出释放；短路不预读，普通回调错误不关闭借用源。以输入消费项而非输出长度计费；调用参数总额继续跨多个展开累计。Agent/Team 效果、busy、所有权、录制范围、尾返回契约和硬取消都属于公共协议，参考[函数组合](FUNCTION_COMPOSITION.md)。

## 推导式实现接口

0.35 新增四种推导表达式与三种子句节点，仍使用 `chaos.program/1`；旧节点 ID、languageVersion 和 HTTP／SDK 接口不变。新类型需要同时通过解析、宏作用域、IR 位置约束、格式化和 IDE 分段作用域验证。原生执行复用迭代协议及调度器，不能以字符串展开或隐藏应用工作流替代。详见[推导式](COMPREHENSIONS.md)。

## 结构绑定实现接口

0.36 的六个 `*_pattern` 节点复用 `p_*` 模式，旧单名称 AST 和标识策略不变。共享纯结构 matcher 先产生临时字典，运行时在冲突检查后统一提交；模块静态声明分析和 IDE 必须遍历所有绑定叶子，不能把记录键或构造器名称当作变量。宏单独注入严格相等、构造器解析与预算回调，纯阶段不能创建运行时 Evaluator 来执行效果。新增语法必须通过 Program 往返、旧完整 JSON 兼容、宏卫生、异步生命周期及真实浏览器测试。参见[解构绑定](PATTERN_BINDINGS.md)。


持久电脑会话由 Engine 管理浏览器生命周期，Store 保存有界元数据与私有浏览器存储。租约归还必须排空已派发动作；失败不能自动重放，requiresReconcile 阻止未核对后的继续行动。公开读取只投影 metadata，不能泄露内部租约、cookies/localStorage 或主机路径。Framework/Client/HTTP/CLI/IDE 读取同一会话记录；真实回归应断言跨轮 DOM nonce、未提交表单、源码采用代次、竞争领取、取消、重启和权限收窄。

## 参数结构与工具一致性

0.38 参数 AST 保留原二元／四元形式，仅带模式时使用 `(name, type, mode, default, pattern)`。Program IR 对应新增可选 `pattern`，模式复用 `p_*`，不可用显示字符串代替结构。调用先完成既有参数绑定和整值检查，再用固定函数捕获环境进行全部无效果匹配；在参数帧和冷 Task／Generator 发布前失败。宏必须保持公开槽标签与内部卫生身份的区别；默认表达式的控制流验证、格式化、IDE 作用域和结构编辑都必须遍历新增模式。原 API／SDK 的调用方法不变。详见[参数解构](PATTERN_BINDINGS.md#函数参数中的模式)。

## 语言服务器与共享源码定位

`chaos lsp` 以标准 Content-Length 帧承载 JSON-RPC。传输、工作区快照和协议适配器分别位于 `lsp_transport.py`、`lsp_workspace.py` 和 `lsp_adapters.py`，生命周期与任务调度位于 `lsp.py`。分析调用 `ide_language.analyze`，格式化调用同一 `format_source`；不执行用户程序，也不调用模型或工具。

`Framework.analyze`、`Client.analyze` 与 `POST /api/v1/language/analyze` 在现有返回值上增加 `definition`：可定位时为 `{file, from, to}`，否则为 `null`。范围相对于目标文件，以 UTF-16 code unit 计数；调用者必须使用同一请求内的 `files` 草稿读取目标。声明来源跟随实际词法绑定及导入，不能把目标模块偏移套用到当前文件。LSP 将这个值转换为文件 URI 和行列；IDE 使用同一值导航。动态未知来源不会伪造位置。

实现的协议方法、版本规则、资源边界和编辑器接入见[语言服务器](LANGUAGE_SERVER.md)。新增协议能力必须先通过真实 stdio 客户端测试，并保持网页、HTTP、SDK 的公共分析结果一致。


## 生成器资源适配器

`std/context` 中的工厂装饰器通过原生 `generator_context` 连接已有生成器与上下文求值；运行时保留内部异常对象，类协议仍接收隔离错误记录。不要在应用层通过 throw_into 转发错误记录模拟同等语义，也不要创建第二个解释器。生成器别名、执行周期、任务组收束与硬控制由内核验证。所有源码／IR／HTTP／SDK 路径共用此实现，详见[资源规范](CONTEXTS.md)。


## 动态资源组合与捕获退出记录

词法 with 和资源栈使用同一获取／退出机制：获取时捕获退出绑定，逆序退出时传递真实内部结果；只有类和显式退出回调接收隔离错误记录。原生句柄保留解释器、执行周期及录制范围，异步操作沿用现有任务调度与收束。新增录制资源必须在 recording.finish 之前完成清理，异常中止后不能在录制外补执行用户回调。接口见[资源栈规范](RESOURCE_STACKS.md)。

## 可恢复智能体与语言工具

函数工具由语言调度器调用，宿主只处理模型／Provider 协议和有界 JSON。每次工具请求让出效果工作线程，避免并发 Agent 等待语言工具时耗尽全部线程。原无绑定 Agent 的接口保持不变；驱动共用原生协议校验和回退逻辑。传输合同不能携带闭包，普通语言返回值也不能先转成句柄描述来绕过 JSON 边界。参见[函数工具](FUNCTION_TOOLS.md)。

## 智能体测试宿主

`evaluation.py` 提供 Program 验收和 IDE 草稿共用的隔离求值入口；fixture 宿主复用语言工具的可恢复协议，闭包由原调度器执行。模型响应和宿主工具回执只能来自严格 JSON 描述，不能取得 Settings 客户端或真实运行 Context。未消费脚本和未声明效果会保留失败记录，清理结束后统一检查。修改此路径须测试 catch 后失败、并发脚本所有权、权限交集和采用 CAS。见[测试规范](AGENT_TESTING.md)。

## Process 演进提交边界

ProcessEvolution 是当前解释器和轮次拥有的冻结值；元数据投影不恢复授权。解释器和宿主清理完成后，使用共用候选测试路径，再在既有 Store 终态事务中写入源码、历史、Process、邮箱和 run 事件。测试/回调不在 SQLite 写事务中执行；提交重查轮次租约、源码 CAS、能力与 Process 定义。注入任一事务写入失败必须回滚全部成功写入，并进入原有失败协调路径。详见[规范与边界](PROCESS_EVOLUTION.md)。

## 0.46 Session 的语言工具协议

独立 BoundSession 通过 session/bound_transition 宿主子操作驱动 native actions，并把 handler 交回原 AST。SessionStore 的 session_tool_bindings 伴随领取事务保存合同身份；旧 session_turns 输入摘要保持不变，避免混用原始/包装输入。历史与分叉复制合同证据，不序列化函数。原 Session 管理与 HTTP/SDK 发送接口不接受 JSON handler。资源、取消及幂等边界见[开发规范](SESSION_TOOLS.md)。

## 0.47 实际角色执行范围

BoundTeam 在 AST 调度器内调用已有 callable，转发参数并等待一层 Task；没有新模型执行器。可信角色范围由执行上下文携带，随延期执行和资源清理恢复，不能由用户输入 _team 安装。普通定义哈希不代表闭包身份。核心边界与例子见[函数角色团队](TEAM_FUNCTIONS.md)。


## 符号索引与候选

`chaos.symbols` 解析原始 AST，不复用类型推导的显示来源作为绑定身份，不执行宏或用户函数。Program 原生函数、HTTP/SDK、LSP、CLI 和编程助手使用这一层。查询若不完整须携带明确标记；任何重命名必须重新校验全包绑定图，前端按原快照原子应用。见[重构规范](SYMBOL_REFACTORING.md)。

## 词法取消保护实现

0.49 的 cancellation 模块维护 CancelShield 的解释器／周期／录制／能力归属和单次词法生命周期；Scheduler 在修改宿主事件、效果等待或组中断之前决定是否暂缓局部请求。延迟请求保留各来源和独立 GroupAbort，最外层退出交付；Task.cancel_event 的身份不能更换。硬控制、周期结束、预算耗尽绕过保护，私有语言调用继续排空并保留迟到硬错误。生成器只拒绝从自身活动保护范围向外 yield，外部保护下的内部生成器消费仍有效。NativeSpec、任务安全快照、IDE 和文档工具共用真实声明，见[语言契约](CANCELLATION.md)。

## 增强赋值实现入口

`operators.py` 维护共享优先级、原位与普通协议表及数值预算；`language.py` 解析并调度 `aug_assign`，捕获 Cell 或接收对象和键，只求值一次。`macros.py` 对展开后的目标做静态验证。Program IR、语义提示、符号索引与前端结构表共用这些语法约束；普通 API 方法和 schema 版本不变。相关行为与并发边界见[增强赋值](AUGMENTED_ASSIGNMENT.md)。

## 结构状态与资源模式

`assign_pattern` 复用 `patterns.match_pattern`，在右侧完成后匹配并预检全部 Cell，然后无挂起地提交。`with_pattern`／`async_with_pattern` 通过统一 `bind_scope_target` 接入普通资源、TaskGroup 和 CancelShield；绑定放在进入后的受保护范围内。宏的赋值遍历复用已有卫生名字，声明遍历生成新名字。Program schema、公开 API 和旧名称节点保持原样，详见[结构语义](PATTERN_BINDINGS.md)。

## 0.52 共享函数签名验证

`chaos.parameters.validate_parameters` 验证 AST 参数形状、模式前缀、默认顺序、剩余槽及槽／模式叶子名字；解析器、Program IR、宏和运行前定义检查共用这一入口。运行时绑定器按 positional_only 消费位置值并保留同名关键字。symbols 只把可具名槽接入函数调用标签图，私有槽仍接受词法捕获与不透明引用检查。IDE、结构编辑与格式化不能通过重排非法模式改变程序含义。

## 0.53 元组实现边界

`chaos.tuple_values.TupleValue` 与宿主 tuple／Node 分开；语言相等在解释器中递归执行，宿主对象相等不替代语言比较。语言值复制、索引、迭代、Channel、JSON 投影和调试递归处理元组元素；权限与严格 JSON 入口不扩大。新增 tuple／p_tuple 节点使用现有 IR/API 版本并经过上下文校验。源码、编辑器、LSP、AgentTool／Team／Session 与 Program 验收共用解释器；规范及可执行示例见[元组](TUPLES.md)。

## 正常完成节点

loop_else(loop,otherwise) 只包装五种既有循环；try_else(body,otherwise) 包装原 try/catch 或 try_cases。otherwise 必须为块，finally 可包 try_else；旧 AST 形状和 API 方法保持兼容。运行时采用每次求值独立的私有完成状态，不从 null 或模型文本推断完成，不能把状态标记暴露为语言值。完整控制、词法与工具链合同见[正常完成分支](CONTROL_COMPLETION.md)。

## 异常扩展的一致性检查

新增 try_cases/rethrow 时，共享 catch 根模式与规范节点验证，保持旧 single-name try。验证 Runner、stream、同步重入、TaskGroup 内部词法载体和 TailCall/pending continuation 的错误上下文隔离。测试覆盖 guard 失败、原异常身份、finally、1200 层旧 handler 尾递归、独立 Task/生成器、宏与 Program 修改；资源出口只接收记录的边界须显式记录。运行全部测试并验证[异常文档](EXCEPTIONS.md)示例。

## 类字段模式的维护合同

`patterns.match_pattern` 通过纯 `project_class(name,value,requestedKeys,include_rest)` 回调取得有效存储字段；Evaluator 负责外层 Class 解析，objects 负责原生身份、遮蔽、权限与预算。不要调用 get_member 或用户协议实现模式。所有绑定位置和参数预检共用该入口；纯宏明确拒绝投影，quote 保留 p_class。IR、格式器、符号分析、静态字段类型和可视字段控件须同时维护，见[类字段模式](CLASS_PATTERNS.md)。

## 集合纯键与源代码闭合

`chaos.set_values` 提供 SetValue 的纯／owned 两种存储及纯 SetBuilder；`chaos.set_runtime` 统一运行时构造和运算，协议表复用 HashMap 的协作式用户哈希、相等、所有权与能力投影。纯键引擎保留无钩子结构比较；协议表复用插入时哈希，公开 hash(Set) 使用统一无序成员哈希。类型标签、有限数与递归键验证共用预算，摘要只用于索引，实际键比较确认碰撞。不要用宿主递归 tuple/frozenset 哈希取代带检查点的比较，否则共享结构或碰撞可绕过预算。set 与 set_comp 节点贯通两种源码方言、宏、IR、语言服务与编辑器；空原生语法使用 `{*[]}`。Channel／调试快照遍历成员；活构造器草稿也登记到统一捕获图，跨 await 不丢失已求值根。纯宏与宿主 SetValue 构造不调用用户协议；严格 JSON 工具入口要求显式投影。完整合同见[集合规范](SETS.md)。

## 可选原生执行后端

`jit.py` 根据受限 AST 构造区间证明、检查点计划及失效指纹；`native_jit.py` 延迟加载 llvmlite，生成、验证并持有宿主机器码。统一 `_s_invoke` 先完成原函数绑定与调用范围，再选择执行路径。原生回调中的异常不得穿过 C ABI：保存后立即返回失败标志，在 Python 边界重新抛出，并保留原检查点来源。不得用执行后的 tick 重放替代实时取消。

Context 的生命周期事件与表达式跟踪需求需区分；普通平台调用可进入 JIT，用户显式跟踪与调试保留解释器。新后端必须比较所有预算阈值、取消位置、返回契约、能力收窄和 AgentTool／Team 调用，而非只比较最终算术结果。AST 或绑定元数据变化须使旧内核失效；代码生命周期不可早于活动函数。仅整数参数／返回值通过 ABI，不把语言堆对象当原生指针。

## 异步 GPU 数值后端

`gpu_compute.py` 在 AST 线程验证 owner、纯算术 Function、卫生参数绑定、精度与规模，捕获不可变数值 IR 和 tuple 数据；不执行函数来猜测纯度。新内部 GPU Effect 在原有最多四个效果 worker 中运行，不能把 Evaluator／Env／Function 或宿主 handler 传入数值 worker。PyTorch 延迟加载，analysis 与显式 CPU 路径不导入后端。

Task 的私有 compute_operation 将新建冷操作纳入 await ownership 和录制范围；已 spawn 的借用 Task 不被等待者自动取消。每周期创建额度、可取消的设备锁、取消后同步清理和有限输出校验应共同测试；不要通过全局 Torch 设置、empty_cache 或 GC 设置改变其他程序。CPU/MPS/CUDA 非逐位浮点等价，矩阵按公开样本容差验收。共享 NativeSpec 定义参数及 awaited 记录字段，IDE、无界面 API 和函数工具共用这些合同。见[GPU 指南](GPU_COMPUTE.md)。

## 隔离 Metal 编译进程

0.62 将 gpu_map 的纯算术 IR 编译为固定 SSA Metal 源码；参数名和常量不会拼接为用户脚本。gpu_metal.py 是可信固定入口，只通过有界 JSON 接收 IR 与数值，无 Evaluator、Env、Task 或提供方凭据。每请求子进程编译、派发、同步、退出；parent 原效果 worker 串行化 MPS 生命周期，取消排空并回收子进程。worker 返回内部纯数据证据，由原 AST 调度线程提交，语言 await 仍得到原五字段结果。源码／IR 哈希用于身份，不表示编译缓存；没有跨请求 Shader 缓存。具体故障和超时边界见[Metal 指南](GPU_METAL.md)。


## 动态恢复与交互式调试

[情况处理与恢复出口](CONDITIONS.md)使用同一求值器、调用绑定器和调度器实现不可变 Condition、动态 handler 和真正展开的 restart_case。透明协议共享逻辑 holder，真实子任务和生成器独立；恢复回调交集工具能力，测试覆盖宏卫生、资源清理和 UI／IR 一致性。


交互式恢复也走原 conditions 请求解析器。活出口只留在解释器 generator 局部，DebugSession 只持有有界脱敏数据和已复制的指令；不要在其 condition 锁内执行 restart test 或用户回调。准备阶段拒绝重入调试，恢复前检查当前 epoch／holder／能力与取消。新的 pauseId／精确 restartId 不是可以跨执行周期恢复的权限令牌。

修改这条路径必须一起验证 Null inline 返回、case 清理顺序、重名／匿名／过滤出口、真实子 Task 隔离、硬控制优先、旧暂停和迟到请求、非法参数保持暂停、桌面／手机及公开 SDK／HTTP／CLI。原 NativeSpec／std/conditions 目录和安装包同步；改基数合同只更新实际新增原语的精确断言，不删旧测试。

## 0.72.0：原生任务局部上下文

[任务局部上下文与动态绑定](DYNAMIC_CONTEXTS.md)说明 ContextVar、ContextToken、ContextScope 和 Context 的实际语言语义：普通任务/生成器创建时浅复制绑定，词法与透明协议调用共享上下文，选定 async 回调原 AST 执行，退出与取消恢复绑定。上下文可保存合法原生语言值，不能扩大工具权限。源码/IR、IDE、CLI、SDK 与 HTTP 共用实现；复制是有界 O(n)，宿主 GC 未被替换。本增量不表示完整 Python/Lisp 生态或自托管已经完成。


## 0.73 原生整数优化

[原生整数 DAG 优化](SCALAR_OPTIMIZER.md)为现有 LLVM 标量区域提供实际常量折叠、CSE 和数据依赖调度。原逻辑检查点、源位置、Task/Context 和效果边界保持不变；普通编译函数可直接成为 AgentTool/Team 的函数实现。compile_optimization_info 是共享 NativeSpecs 的只读查询，旧 compile_function/compile_info 不变。它不表示整程序指令重排、链接删除、原生循环/AOT、自研 GC 或完整 Python/Lisp 能力已经完成。


## 0.74 可变控制流中的原生区域

[可变控制流中的原生计算区域](NATIVE_CONTROL_REGIONS.md)将显式 auto-control/native-control 配置接入原 compile_function 两参数接口。原 Function/AST/Cell、同步迭代器、循环耗尽 else、break/continue/return 与真实 CHECKPOINT 保持不变；选中表达式逐次读取当前值并进入受守卫的 LLVM 内核。增强赋值只编译 RHS；严格 Bool、短路与相等使用原协议。旧 auto/native/interpreter 的范围、默认行为、compile_info 12 字段和优化查询保持兼容。此区域模式本身不实现整个循环 CFG。显式 auto-flow/native-flow 的[原生结构控制流](NATIVE_FLOW.md)另将 while／同步 for 和原结构模式降为 PC CFG，来源、游标协议、函数体调用和所有宿主语义事件仍经过原调度器。无标量区域不阻止生成 CFG；完整参数绑定不借用标量八输入限制。受保护子树整体保留，尾位置由原结构传递，不能把所有调用都标为尾调用或按名称猜测纯函数。控制帧共用 capture_frames 生命周期，保留当前模块／环境／游标／项并释放已消费的历史根；扩展必须比较解释器、便携及真实 LLVM 的检查点、取消、权限和来源，不可另造迭代解释器。AOT、自研 GC、完整 Python/Lisp 与自举仍未完成。


## 项目环境检测与部署契约

[环境检测与配置](DEPLOYMENT.md)的 `setup`／`configure`／`doctor` 统一进入 `chaos.project_deployment`，不创建第二套运行时或模型路由。启动器持有项目环境锁，standard 依赖在临时目录安装并用 `-I -S` 子进程验证后才切换；core 直接验证无第三方依赖的语言核心。自检运行固定递归、跨模块和异步程序，不执行用户入口。JSON 报告使用 `chaos.deployment-report/1`，进度写 stderr，失败返回非零状态。

`.chaos/deployment.json`（`chaos.deployment/1`）保存 profile、aiAdvice、环境指纹与最近成功检查；`.chaos/settings.json` 继续由原 Settings 的版本比较、文件锁和原子写入管理，角色与智能体覆盖优先级不变。硬件存在不等于能力授权。doctor 不写这些持久配置，但可创建协调用的环境锁文件。

环境依赖标记包含解释器 ABI、平台与 requirements；不能用全局 site-packages 替代目录包的缺失依赖。已安装 SDK 项目使用宿主已有依赖，安装或更新由宿主管理；目录包可以独立配置。替换时保留兼容启动器的原字节。0.153 以前的启动器可用 `setup --yes` 进入新流程；这类启动器在共享锁下调用 configure 会明确拒绝并给出此命令。0.153 起的官方启动器已为 setup/configure 取得独占锁。

测试部署修改时覆盖临时依赖自检失败保留旧环境、配置输入中断、JSON 输出、无颜色终端、纯核心隔离、依赖指纹失配、旧包升级与回退保留设置。模型建议仅在用户选择后使用既有规划层，发送白名单系统概况，建议不直接执行；自检不包含付费调用。跨平台分支模拟不替代 Windows／Linux 实机验收。

部署建议通过 `Settings.resolve('intelligence-deployment', 'planner')`、`plugin_host.require('native')` 和共享 NativeHarness 运行，不维护另一套 completion／失败切换循环。它只在局部运行配置限制 `maxSteps=1`、`timeout<=20`、`maxOutputTokens=384`，不写回用户设置；NativeHarness 在创建客户端前验证可选 `maxOutputTokens` 为 1–65536 的整数，并在每次候选请求中传递 `max_tokens`，未设置时保持原请求行为。无工具运行仍验证模型响应协议；后端不支持、插件不可用或模型失败仅影响建议结果，离线部署检查继续有效。

## 命令行、项目上下文与内置智能帮助

`std/intelligence` 导出普通的 Agent 值；`chaos ask` 和 REPL `.ask` 将显式问题作为 JSON 数据传给同一个语言程序，继续走 Settings、Harness 和能力检查。CLI 不直接维护模型会话或复制模型客户端。需要项目草稿和验收工具时，`chaos assist` 使用现有 Client 和 IDE 助手 API，任务、事件与应用提案的版本检查均由服务管理。

项目命令复用各入口的参数解析和执行函数。先解析参数，再解析项目位置；帮助或参数错误不创建项目状态、不发起模型调用。已安装 CLI 可以用 `--directory` 选择项目。目录启动器已绑定自身活动运行时及环境锁，不能用该参数跨工程执行；应调用目标项目自己的启动器。项目内相对配置路径相对于项目根目录，调用返回或失败后恢复原 cwd 与 CHAOS_CONFIG。

`Documentation.source(topic)` 从注册目录读取 Markdown；CLI 的 `docs list`／`docs show` 和 ask 的 `--topic` 使用同一安装版参考，不依赖 HTML 渲染器。网页和静态导出继续使用原渲染与链接检查。前者不执行文档例子；`docs check` 才负责示例与链接验收。每个示例最多执行 30,000 步、10 秒，并比较实际结果；这个检查用于验证功能，不是两秒内完成的性能承诺。教程与命令输出契约见[命令行教程](COMMAND_LINE.md)。
