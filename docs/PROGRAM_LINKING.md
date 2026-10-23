# Program 库链接与工程复用

把一组 Chaos 模块放到另一个工程时，可以先把库保存为闭合的 Program，再把它挂载到消费者的逻辑目录。`program_link(definition, libraries)` 返回新的 Program；源码、宏、方言、类型和 AgentTool 实现继续进入原解释器，不需要额外应用端模块系统。

## 两个同名文件的库

```chaos example=program-link-libraries
let increment=program_from({
  kind:"program",entry:"main.chaos",
  files:{
    "main.chaos":"import \"util.chaos\" as U;fn apply(x){U.step(x)};export{apply}",
    "util.chaos":"fn step(x){x+1};export{step}"
  },languageVersions:{"main.chaos":2,"util.chaos":2}
});
let combined=program_link({
  kind:"program",entry:"main.chaos",
  files:{"main.chaos":"import \"vendor/left/main.chaos\" as L;import \"vendor/right/main.chaos\" as R;fn solve(x){R.apply(L.apply(x))};export{solve}"},
  languageVersions:{"main.chaos":2}
},{"vendor/left":increment,"vendor/right":increment});
[module_from(combined).solve(40),length(program_info(combined).files)]
```

结果为 `[42,5]`。两个库都保留自己的 `main.chaos` 和 `util.chaos`，前缀决定它们在新包中的身份；`"util.chaos"` 仍相对于各自导入者解析。相同库挂载两次产生两组模块路径，不能假定其类身份或词法状态共享。

| 接口 | 合同 |
|---|---|
| `program_link(definition: Record, libraries: Record) -> Program` | definition 是原四字段定义；libraries 是挂载目录到当前解释器 owned Program 的记录 |
| Python `link_program(definition, library_definitions, *, checkpoint=None)` | 使用相同链接器；库值是原四字段定义数据，返回新的规范四字段定义 |

消费者 definition 严格包含 `kind:"program"`、`entry`、`files`、`languageVersions`。入口须在消费者文件中，版本映射精确覆盖文件；它可以包含将在本次链接中满足的导入，所以不要求先对消费者调用 `program_from`。库自身必须是闭合程序。库的 entry 不会自动变成公开 API；消费者明确 import 所需文件，再使用其 export。

挂载是非空、规范、包内 POSIX 相对目录，不得占用 `std` 命名空间。挂载之间不能互为祖先目录；消费者文件不能占据挂载命名空间。重复文件、文件与目录冲突均被拒绝，不通过覆盖顺序决定结果。空 libraries 仍验证消费者闭合性。

文件名遵循原 ProgramBundle 的精确字符串规则；不会按大小写或 Unicode 规范化去猜同一文件。逻辑包可以整体以定义 JSON 保存，但不承诺把每个逻辑模块写到任意宿主文件系统时都没有名称别名。

## 已链接的库可以继续组合

```chaos example=program-link-nested
let base=program_from({kind:"program",entry:"main.chaos",
  files:{"main.chaos":"fn step(x){x+1};export{step}"},
  languageVersions:{"main.chaos":2}});
let worker=program_link({kind:"program",entry:"main.chaos",
  files:{"main.chaos":"import \"internal/base/main.chaos\" as B;fn solve(x){B.step(x)+1};export{solve}"},
  languageVersions:{"main.chaos":2}},{"internal/base":base});
let application=program_link({kind:"program",entry:"main.chaos",
  files:{"main.chaos":"import \"vendor/worker/main.chaos\" as W;export{W}"},
  languageVersions:{"main.chaos":2}},{"vendor/worker":worker});
[module_from(application).W.solve(40),length(program_info(application).files)]
```

结果为 `[42,3]`。这里先构造闭合 worker，再整体挂载；不是在同一 libraries 记录中放入重叠目录。所有源码字符串逐字保留，逐文件语言版本随完整路径迁移；不会改写 import 文本、格式化旧模块或把方言1升级成方言2。标准库导入仍使用原 `std/...` 解析器。

## 链接不会执行模块

```chaos example=program-link-no-initialization
let library=program_from({kind:"program",entry:"main.chaos",
  files:{"main.chaos":"throw \"initialization must be explicit\";let answer=42;export{answer}"},
  languageVersions:{"main.chaos":2}});
let linked=program_link({kind:"program",entry:"main.chaos",
  files:{"main.chaos":"import \"vendor/library/main.chaos\" as L;export{L}"},
  languageVersions:{"main.chaos":2}},{"vendor/library":library});
[type_of(linked),program_metadata(linked).origin,length(program_info(linked).files)]
```

结果为 `["Program",null,2]`。链接先检查原始结构与总规模，再逐库独立验证闭合性，最后用合并后的 ProgramBundle 验证整个包，包括未导入文件、依赖环、语法、导出和卫生宏。它不运行模块初始化，不读取宿主文件、不下载依赖、不调用模型。非法候选不会部分修改旧 Program 或消费者定义。

`module_from(linked)` 才在当前解释器初始化模块并返回真实导出；`linked(input)` 创建原 Program 冷任务，实际执行需原项目宿主。链接结果没有可信项目 origin，也不会继承库的旧 origin；采用仍需以实际当前 Program 为 base，经过原测试、权限和版本 CAS。已有模块实例、函数、运行和配置不被热替换。

## 给智能体使用库函数

链接后的普通导出函数可作为 AgentTool 或 Team 角色实现。下面的 Python 示例注册确定性提供方，真正调用库中的语言函数；不需要模型凭据：

```python
from chaos import Framework, Registry, ProviderSpec, Settings, ToolCall, Final, link_program

library = {"kind":"program", "entry":"main.chaos",
           "files":{"main.chaos":"fn solve(x:Int)->Int{x+2};export{solve}"},
           "languageVersions":{"main.chaos":2}}
consumer = {"kind":"program", "entry":"main.chaos",
    "files":{"main.chaos":'''import "vendor/worker/main.chaos" as W;
agent worker{provider:"choose",tools:[]};
let bound=agent_bind(worker,[agent_tool("solve",W.solve,input_type="Int",output_type="Int")]);
await bound(input)'''}, "languageVersions":{"main.chaos":2}}
def choose(request, context):
    context.checkpoint()
    return ToolCall("solve", request.input) if not request.history else Final(request.history[-1]["output"])
registry=Registry()
registry.register_provider(ProviderSpec("choose", choose))
linked=link_program(consumer, library_definitions={"vendor/worker":library})
with Framework(registry=registry, settings=Settings(":memory:")) as app:
    project=app.create_project("linked-agent", source=linked["files"][linked["entry"]],
        files=linked["files"], entry=linked["entry"], capabilities=[])
    assert app.start(project["id"], project["version"], 40).result(10) == 42
```

这个新项目示例的所有模块都使用方言2；create_project 不接收 language_versions。已有混合方言定义应保留完整四字段，经 ProgramBundle 的 language_versions 参数执行，或通过原 validate_program／adopt_program 管理项目候选。

库交付的是源码，不包含活闭包、宿主回调、密钥或运行授权。上例的库函数仍由原 AgentTool 调度器执行，当前项目、角色和工具能力限制继续生效。链接不会把受信任 Python 回调变成沙箱，也不是包注册中心、依赖求解器或模型自动下载器。

## 工具链与交付边界

`program_link` 是普通原生函数，没有新关键字或 IR 节点。NativeSpecs 提供真实参数与 Program 返回类型；IDE、LSP、高亮和格式化不会为推断库导出而执行链接或初始化模块。动态构造的链接结果只能得到保守类型提示；要分析具体多文件源码，可将 Python 返回的 files、entry 和 languageVersions 交给原 `Framework.analyze`。

源码中的 program_link 可由原 CLI、SDK 和 HTTP 执行；Python 链接结果可经公开 ProgramBundle 执行，或作为完整定义交给原 Program 验证／采用接口，不新增远程链接服务。Python 的 link_program 是纯组装函数，读文件和保存产物由宿主明确决定；可复制安装沿用[安装指南](INSTALLATION.md#作为完整包放进其他项目)，无需保留开发仓库。完整例子见[program-link.chaos](../examples/program-link.chaos)。

合并后仍受原程序包总限制：最多64文件、每文件100000字符、UTF-8源码合计1000000字节、依赖深度32，以及原定义 JSON 和解析限制。各库先单独验证，最终整包还要通过一次统一的宏展开预算；一个独立有效的库仍可能让最终包超限。语言内调用沿用当前 Evaluator 的总步数、超时和取消检查；纯 Python SDK 可提供 checkpoint 接入宿主控制，省略时不建立覆盖所有库的额外执行步数预算。完整 Python／Lisp、自动依赖版本求解、跨进程活栈迁移与解释器自举不由此完成。
