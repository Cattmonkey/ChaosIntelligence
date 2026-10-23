# 增量编译与程序分析

0.58 提供实际接入 `ProgramBundle` 的逐模块解析缓存，以及同源的编译报告。报告可由语言函数、系统智能体、IDE、SDK、HTTP 和 CLI 读取。本静态报告的目标是 `portable-ast`，不生成机器码；0.59 的运行期 [compile_function](NATIVE_FUNCTIONS.md) 可为受约束的整数函数生成本机代码，两者使用同一语言调用语义。原生标量及 flow 后端现在复用精确匹配的有界机器码制品；这不缓存本页的公开 AST 分析，也不改变逐模块解析和宏的预算合同，见[原生编译复用](NATIVE_FUNCTIONS.md#有界原生编译制品复用)。

## 语言自己检查程序

```chaos example=compiler-self
let report = program_compile(current_program());
[report.schema, report.target, report.entry, report.summary.moduleCount]
```

结果为 `["chaos.compilation/1","portable-ast","main.chaos",1]`。`program_compile(value: Program) -> Record` 接收本解释器拥有的 Program，检查完整源码快照并返回新记录。它不运行被分析程序，不调用其中的模型、工具、用户方法或提供方；它会运行原有受限的纯宏展开。编译错误仍抛出原有语言错误。

```chaos example=compiler-candidate
let candidate = program_from({
  kind: "program", entry: "main.chaos",
  files: {"main.chaos": "unknown_worker(input)"},
  languageVersions: {"main.chaos": 2}
});
let report = program_compile(candidate);
[contains(report.summary.effects, "call"), contains(report.summary.effects, "unknown")]
```

结果为 `[true,true]`。静态编译不会执行 `unknown_worker`，也不保证这个动态名称在运行时存在。名字看起来像内置函数，不足以证明调用纯净，因为函数可以被遮蔽或以参数传入。

程序可以把报告交给普通 Agent、语言函数或 Team 角色决定下一步；编译报告不授予它们修改项目的权限。修改仍产生 Program 候选，通过 `program_probe`／现有测试、`program_adopt` 或 ProcessEvolution 的版本与权限检查采用。系统智能体的 `draft.compile` 读取同一报告，不能凭报告跳过测试。

## 编译路径

| 阶段 | 当前行为 |
|---|---|
| 输入验证 | 校验完整包、入口镜像、文件大小和各文件方言 |
| 增量解析 | 按编译器 ABI、模块路径、方言及精确源码复用成功的原始解析树 |
| 链接 | 每次重新检查导入、导出、依赖深度和循环；孤立模块也必须合法 |
| 宏展开 | 每次对整个包重新展开，保留跨模块宏和全局卫生命名语义 |
| 结构分析 | 按真实展开 AST 统计潜在效果，区分函数定义时求值与函数体 |
| 执行 | 继续由原解释器为每轮创建环境、闭包、智能体和模块结果 |

修改一个依赖模块时，未修改模块的原始解析树可以命中；其依赖哈希和整个包的编译身份会改变。没有复用旧的宏展开结果、旧 Env、执行结果或权限。缓存中的 AST 与公开返回的 AST 分别复制，调用方改动可变列表或源码位置不会污染下一次编译。

缓存为进程内有界 LRU，默认最多 128 项、2,000,000 源码字节和 200,000 AST 逻辑单位；逻辑单位计入容器、元数据与标量大小，不能当作精确宿主堆字节。超大树照常编译而不保留。只有成功解析的完整单元可被保留；后续链接失败不妨碍重用其原始解析，但下一次仍会重新执行链接和宏检查。服务重启清空缓存，不产生历史磁盘缓存残留。

带 `checkpoint` 的编译完全绕过缓存读写，保留已有 parser／macro 的检查次数与错误位置。因此 `program_compile` 和带共享预算的智能体草稿编译当前不通过缓存省略工作。普通无检查点编译、IDE 编译和相应 SDK 路径可以命中。命中次数不是执行步数，也不是加速倍数。

## 报告身份和效果

`chaos.compilation/1` 包含：

- `compiler`：软件版本与前端 ABI；`target`：当前目标。
- `artifactHash`：全部包文件、引用到的标准模块、方言、入口和编译器身份的确定性摘要，与冷／热命中状态无关。它是源码编译身份，不是机器码、优化正确性证明或可执行权限；不同于 Program 的 `definitionHash`。
- `modules`：各模块 `sourceHash`、`unitId`、传递 `dependencyHash`、依赖列表、导出、宏导出、`cacheStatus` 及结构分析。
- `summary`：模块与节点数量、命中／未命中／绕过数量、立即与延迟效果集合。
- `passes`、`limitations`：实际完成阶段和明确的限制。

`effects` 表示结构上可能立即出现的操作；`latentEffects` 表示函数体中可能出现的操作。默认参数在定义时求值，仍计入立即效果；类的基类、成员初值、方法默认值和装饰器也不会被整个隐藏进延迟区域。两边分支都统计，惰性推导保守多报；这不是路径敏感的完整效果类型系统。

`call`／`unknown` 表示未知动态调用，`protocol` 包含可能运行用户方法的运算、索引和迭代，`suspend` 表示挂起结构；另有读取、绑定、状态、分配、控制、异常、模块及资源类别。新增或未识别的 AST 种类一律保守记为 `unknown`。即使没有 `unknown`，预算、异常和调度点也仍是可观察行为；当前报告不授权指令重排或删除代码。包级字面量运算特化保留这些观察点，见下节。

## SDK、命令行与服务

```python
from chaos import compile_program, Framework
from chaos.compilation_cache import CompilationCache

cache = CompilationCache()
source = "fn square(x) { x * x }; square(7)"
cold = compile_program(source, compiler_cache=cache)
warm = compile_program(source, compiler_cache=cache)
assert cold["artifactHash"] == warm["artifactHash"]
assert warm["summary"]["cacheHits"] == 1
assert Framework.evaluate(source) == 49
cache.clear()
```

`CompilationCache.info()` 只返回聚合计数和上限。SDK 可显式 `compiler_cache=None` 禁用缓存；构造器上限不得超过 256 项、4,000,000 源码字节、400,000 AST 逻辑单位。公开 `compile_bundle(bundle)` 分析已经构建的 ProgramBundle，不重复宏展开。调用方若随后修改公开 AST，报告分析会重算，但源码身份仍指向原输入，不能作为修改后 AST 的执行证明。

```sh
python -m chaos compile examples/compiler.chaos
python -m chaos check examples/compiler.chaos
python -m chaos run examples/compiler.chaos
```

`compile` 输出 JSON 报告，支持文件／标准输入和 `--root`；它不保存项目、不运行程序。CLI 会复用已构建的包。`check`、`ir`、`run` 的原调用方式保持不变。编译报告不能作为 `run` 的可执行输入；继续运行原源码包。另提供显式 artifact build／validate／run，不能混用两种 JSON 格式。

`Framework.compile(source, files=..., entry=..., language_versions=...)` 与 `Client.compile(...)` 使用同一报告。HTTP 为 `POST /api/v1/language/compile`，请求字段为 `source`、可选 `files`、`entry`、`languageVersions`；拒绝未知字段。`source` 是入口源码的权威镜像，不能传成当前打开的依赖文件。

IDE 源码区的“编译报告”携带完整未保存草稿和各文件方言，展示目标、缓存、依赖与效果；不自动保存或修改源码。编辑、切换文件／项目、方言或版本变化后，旧请求不能覆盖新草稿的报告。`program_compile` 使用共享原生函数描述，支持代码高亮、参数帮助、返回记录字段提示和保真格式化。

## 当前边界

已实现增量解析、内容寻址依赖身份、保守结构效果分析，以及下面的包级字面量与不可变词法整数特化。尚未实现增量宏展开、跨过程纯度证明、整程序死代码删除与指令重排、通用 JIT／AOT、自研 GC 和通用 GPU 编译。0.73 已有区域整数 DAG 调度，Metal 已有独立受限 GPU 控制流编译。0.59 已另行实现带守卫和精确检查点的可选整数函数 JIT，详见[原生函数](NATIVE_FUNCTIONS.md)。执行语义仍以[语言参考](LANGUAGE.md)为准；具体后端与运行时设计见[编译与运行时演进](COMPILER_RUNTIME_DESIGN.md)。图灵完备性不推出编译优化的正确性、性能或完整 Python／Lisp 兼容。


## 0.73 原生整数优化

[原生整数 DAG 优化](SCALAR_OPTIMIZER.md)为现有 LLVM 标量区域提供实际常量折叠、CSE 和数据依赖调度。原逻辑检查点、源位置、Task/Context 和效果边界保持不变；普通编译函数可直接成为 AgentTool/Team 的函数实现。compile_optimization_info 是共享 NativeSpecs 的只读查询，旧 compile_function/compile_info 不变。它不表示整程序指令重排、链接删除、原生循环/AOT、自研 GC 或完整 Python/Lisp 能力已经完成。


## 0.74 可变控制流中的原生区域

[可变控制流中的原生计算区域](NATIVE_CONTROL_REGIONS.md)将显式 auto-control/native-control 配置接入原 compile_function 两参数接口。原 Function/AST/Cell、同步迭代器、循环耗尽 else、break/continue/return 与真实 CHECKPOINT 保持不变；选中表达式逐次读取当前值并进入受守卫的 LLVM 内核。增强赋值只编译 RHS；严格 Bool、短路与相等使用原协议。旧 auto/native/interpreter 的范围、默认行为、compile_info 12 字段和优化查询保持兼容。此实现不是整个循环 CFG 的机器分支、可恢复 PC ABI、AOT、自研 GC、完整 Python/Lisp 或自举。


## 0.75 可恢复结构控制流

[可恢复的原生结构控制流](NATIVE_FLOW.md)增加显式 auto-flow/native-flow。LLVM 保存 PC 并选择结构 CFG 分支，返回宿主后执行原叶子、Env/Cell 和真实 CHECKPOINT；调试/追踪使用同一续接 IR 在当前位置切换便携派发。新模式一次调用要求稳定定义，执行中结构检查点检测到 AST 改动安全报 definition_changed；旧五个目标不变。完整 Python/Lisp、自举、通用活栈迁移、AOT/跨目标 ABI、自研 GC/CUDA 编译仍未完成。


## 包级字面量与不可变词法整数特化

链接和宏展开后，`ProgramBundle` 为全部模块、普通函数、生成器、异步函数及默认参数建立私有计划。`guarded-total-int/2` 在原字面量特化上识别简单不可变let的已知整数、别名与定义时闭包快照，参数和局部绑定遮蔽外层事实；默认值在定义点分析，函数体按真实参数身份遮蔽。只接受精确 `Int` 的二元 `+`、`-`、`*` 和一元 `+`、`-`，每个输入及中间结果最多256 bit；超出证明或分析配额的部分继续走原解释路径。**名称读取仍实际执行**，只在原运算入口经实际操作数守卫后复用结果。Cell、函数调用结果、用户运算协议、浮点、除法、容器和比较不被预计算。它不执行待分析程序，也不因未选分支包含 `1/0` 提前报错。

这项优化默认进入同一个执行器：原节点、左右操作数求值、执行帧和 ProtocolBridge 保留，原运算入口先执行同一个 `tick`，再校验站点身份、运算符与本次操作数的精确类型和值。命中时直接使用编译期结果，避免重复算术；失败时只解释当前运算，不重放前缀。模块初始化、闭包快照、Agent/Tool 调用、Task、取消与资源清理仍沿原路径。调试和追踪保留全部原节点及位置。源码、IR、Program 定义哈希和格式化结果不因计划改变。

```chaos example=compiler-literal-pass
let candidate = program_from({kind:"program",entry:"main.chaos",files:{"main.chaos":"fn calc(){(2+3)*4};calc()"},languageVersions:{"main.chaos":2}});
let report = program_compile(candidate);
let stage = filter(fn(p){p.name == "literal-operation-specialization"}, report.passes)[0];
[stage.status, stage.candidateSites, stage.checkpointPolicy]
```

编译报告中的 `candidateSites` 是静态机会，不是运行命中数或速度证据。报告保留 `instruction-reordering: not-implemented`；该特化不移动语句、删除模块或声称跨过程纯度证明。`artifactHash` 纳入特化算法身份，仍不是执行权限。显式编译会为新分析阶段计入共享编译预算；普通运行没有为计划构造额外调用求值器的执行期 `tick`，分析自身受16384项、4096站点的配额限制。

低层 Python SDK 可以使用 `Evaluator(optimize_literals=False)` 或 `Framework.evaluate(..., optimize_literals=False)` 选择参照执行。`Evaluator.optimization_info()` 返回启用状态、当前计划候选数、从最近一次运行开始的实际 `appliedOperations` 和 `guardMisses` 聚合计数，不暴露 AST 或常量值；关闭优化时这两个执行计数为零。编译计划最多保留4096站点，没有磁盘缓存或旧执行环境。已消除的算术次数不等于性能加速倍数；仍需分别测量编译与执行耗时。


不可变词法事实只来自实际展开AST；模块各自分析，不从同名字符串推断跨模块值。宏参数使用实际卫生名称，未知绑定结构保守放弃继承事实；共享AST或不明确上下文不作为词法常量证据。预扫描、作用域和事实查找受同一个有界结构工作配额控制，不读取运行Env、不执行用户钩子，也不为普通执行添加分析tick。保守遮蔽只减少优化机会，不改变语言的绑定／快照规则或引入变量提升。

```chaos
let factor=7;
let alias=factor;
fn work(value:Int){value+(alias*6)};
[work(0),work(1)]
```

结果仍为 `[42,43]`。该例可复用两次乘法结果，参数相关的加法继续实际执行；`optimization_info()`的实际命中与物理运算差分测试证明少算，不代表未经测量的耗时加速。公开AST在建计划后被宿主修改时，实际类型／值守卫失败只回退当前操作，名称读取、源位置和前面的工具效果不会重放。

## 可验证的源码型编译产物

`compile_artifact(source, files=None, entry="main.chaos", *, language_versions=None)` 返回 `chaos.compiled-artifact/1` JSON 数据；`load_artifact(data)` 通过同一实现返回新的原 `ProgramBundle`。产物保存闭合源码、方言、编译器／标准模块／依赖身份及可重定位的便携流模板，不保存 Env、Cell、Function、Task、能力、运行计数、PC 或原生地址。它包含源码，应按源码的保密要求保存。

装载会重新解析、链接、展开宏并确定性降低，逐项验证保存的模板、效果与节点路径；自行重算摘要不能使被修改的 CFG 合法。加载不是前端绕过，也不是 AOT 或执行授权。模块初始化、默认值及工具等运行期效果只在原执行路径实际到达时发生，重复装载和不同求值器保持独立环境。

模板仅在源码明确调用原 `compile_function(..., target="auto-flow" 或 "native-flow")` 且函数匹配时由原编译入口采用；函数形成不会自动编译。解释目标、未匹配的动态函数与不受支持部分保留原行为；实际 LLVM 准入仍取决于宿主可选依赖。旧 `chaos.compilation/1` 报告及 `compile_program`／`Framework.compile`／原 `chaos compile` 不变，报告不是新的运行输入。

SDK 导出同名两个函数；`Framework.compile_artifact`／`Framework.load_artifact` 使用同一实现，`Framework.validate_artifact` 只返回原包摘要。显式执行可以使用 `Framework.evaluate(load_artifact(data), bindings={"input": 40})`，仍是原纯求值合同，不新增工具、模型或设备权限。

命令行 `chaos artifact build FILE --root DIR` 向标准输出写 JSON；`chaos artifact validate artifact.json` 重新验证并输出包摘要；`chaos artifact run artifact.json --input-json 40` 才显式验证并纯求值。JSON 文件参数也接受 `-` 读取标准输入；命令不改写输入文件，不隐式加载宿主凭据或运行用户项目。

只读 HTTP 提供 `POST /api/v1/language/compile-artifact`（原 source／files／entry／languageVersions 请求）和 `POST /api/v1/language/validate-artifact`（仅 artifact 字段）；`Client.compile_artifact`／`Client.validate_artifact` 对应这两个入口。没有 HTTP 执行产物接口。HTTP 沿用原请求 4 MB／300000 数据值与响应 8 MB 限额；本地 SDK／CLI 的核心产物上限为 canonical JSON 8 MB／400000 值／256 层、512 模板及32768总指令，单模板仍受原1024指令等配额约束。CLI 输入文件另限原始 UTF-8 JSON 8 MB，带额外空白的文件也计入该输入限制。较大的本地合法产物不保证能通过更小的 HTTP 请求边界。

本节描述当前接口；具体版本的测试、分发和安装仍分别验收，不声称加载提速、跨目标原生 ABI、跨资源活栈持久化、独立 GC、完整 Python／Lisp 或物理机器人能力已经完成。AgentTool／Team／Bot 与 Robot 软件动作继续使用原运行时、权限、取消和不重放合同。
