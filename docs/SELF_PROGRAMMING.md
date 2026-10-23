# Program：语言内的自编程与版本演化

0.22 把整个闭合程序包实现为一等 `Program` 值。一个用 Chaos 编写的 dot 可以读取自己的真实运行源码、让 Agent 提出候选、用语言本身测试候选，然后采用为当前项目的新版本。相同机制用于没有网页、没有 IDE、没有 Codex App 的 Framework、CLI 和 HTTP 环境。

函数式编程提供高阶函数、组合和不可变值，但不自动带来自修改。这里增加的是明确的反射、程序表示、隔离执行和版本提交语义。修改产生新 Program；旧值、已导入的函数和正在执行的调用栈保持原状。

## 程序值与真实反射

```chaos example=program-definition
let p=program_from({kind:"program",entry:"main.chaos",files:{"main.chaos":"input+2"},languageVersions:{"main.chaos":2}});
[type_of(p),program_info(p).entry,type_of(p(40)),program_metadata(p).origin]
```

结果 `["Program","main.chaos","Task",null]`。定义只有 `kind`、`entry`、`files`、`languageVersions` 四个字段；入口必须存在，版本映射覆盖每个文件。构造时解析、链接和展开整个程序包，包括未被入口导入的模块，不执行模块代码。循环导入、无效路径、语法错误和宏错误均会拒绝候选。

```chaos example=program-reflection
let self=current_program();
let ir=program_ir(self);
[type_of(self),program_info(self).entry,ir.schema,ir.root.kind]
```

结果 `["Program","main.chaos","chaos.program/1","block"]`。`current_program()` 读取本解释器实际执行的 ProgramBundle，不依据提示词、字符串内容或函数名称猜测程序。嵌套执行候选时，它读取候选自己的模块包。

无源码 executable 镜像不能重建原始源码；其中 `current_program()` 明确抛出 `program_error`，不借用授权项目的旧源码冒认实际镜像。镜像仍可显式构造并执行 source Program；该子调用反射自己的源码定义，来源按原规则检查。镜像身份、项目授权和源码来源的区别见[编译运行时设计](COMPILER_RUNTIME_DESIGN.md)。

`program_metadata(value)` 返回 `{definitionHash,origin}`。哈希包含准确源码、入口及每个模块的语言版本；不是格式无关的语义等价证明。持久项目运行的原始 Program 带受控 origin `{projectId,version,definitionHash}`；纯求值和普通 `program_from` 的 origin 为 null。`program_edit` 派生值保留基准来源，但源码哈希已改变，因此不能充当采用操作的 base。执行另一个候选时，其内部 `current_program()` 仅在实际程序哈希与受控项目快照完全相同时附带 origin，否则为 null。来源只是版本身份，不授予权限。

Program 满足 `Function` 契约，可通过 `partial`、`apply`、普通装饰器、静态方法、Plan 和 Goal 组合。`p(input)` 与 `run_program(p,input)` 都创建冷 Task，等待后才执行；没有宿主的纯求值环境允许构造、反射和创建冷任务，实际运行需要项目宿主。`chaos run` 已使用 Framework 宿主。

## 复用闭合程序库

`program_link(definition, libraries)` 将消费者四字段定义与挂载的 Program 库组成新的闭合程序。库文件只增加逻辑路径前缀，源码和逐文件方言保持原样；链接验证但不初始化模块，也不读磁盘或网络。消费者可以显式 import 多个库中的同名内部文件，或复用已链接的库。结果 origin 为 null，旧值与已运行实例保持原样；采用仍以真实当前 Program 为 base。Python 的 `link_program` 接收定义数据，便于工程师把库放进其他项目。路径、规模和执行合同见[Program 库链接](PROGRAM_LINKING.md)。

## 结构化改造

`program_info(value)` 返回定义副本；`program_ir(value,file=null)` 返回已有 `chaos.program/1` 语法树；`program_edit(value,file,operations)` 复用现有结构编辑器的节点操作，返回经过整个包验证的新 Program。

```chaos example=program-edit
let old=program_from({kind:"program",entry:"main.chaos",files:{"main.chaos":"40"},languageVersions:{"main.chaos":2}});
let node=program_ir(old).root.statements[0];
let revised=program_edit(old,"main.chaos",[{op:"set",id:node.id,field:"value",value:42}]);
[program_ir(old).root.statements[0].value,program_ir(revised).root.statements[0].value]
```

结果 `[40,42]`。结构修改会把被编辑模块规范为方言 2，未改模块保留原版本；旧字段索引渲染为 `.[]`，避免改变方言 1 的既有含义。也可用普通记录/列表运算生成新定义，再 `program_from`，不需要自然语言关键词提取规则。

`program_source(value,name="saved_program")` 导出普通模块 `let saved_program=program_from(...);export {saved_program};`，支持保存、导入及再次验证。JSON 标记仅描述 Program，不会自动恢复可执行对象。跨解释器必须显式传定义；Function、Task、实例和环境不能进入 Program 输入输出。

## 候选执行、测试和采用

`await run_program(candidate,input)` 在新的解释器、模块环境和调度器里执行，输出必须是 JSON。它继承调用方的项目工具范围、Team 限制、总预算和取消信号；没有新权限。每个子调用有独立 `callId` 和父调用来源，最多嵌套 4 层，同一运行有 64 次调用上限。

`await program_probe(candidate,cases)` 实际运行 1–16 个测试。每项严格为 `{input,expect,entry?,fixtures?}`；`entry` 可选择候选中的测试模块。省略 fixtures 时保持纯求值；提供显式 fixtures 后，可验证普通 Agent、绑定的真实语言函数及声明的工具回执。每项使用新解释器，不访问生产模型或真实宿主效果，不能靠模型的“通过”文字判定。完整协议与限制见[智能体协议测试](AGENT_TESTING.md)。

报告包含候选哈希、测试哈希、逐项结果和 `passed`。比较严格区分布尔值和数值。普通运行错误形成失败项；资源耗尽与取消必须终止操作，不能被转换成通过或普通失败后继续执行。测试证明的是这些输入上的结果，不证明所有输入正确，也不证明候选普遍优于旧程序。

`await program_adopt(base,candidate,cases,reason="")` 是语言级持久化效果，成功返回紧凑的 receipt（包含新旧版本、定义哈希、测试数量及运行身份），不返回整个项目；SDK/HTTP 返回 `{project,receipt}`：

1. base 必须是当前项目原始运行 Program，带匹配的受控 origin；改造后的值不能冒充基准。
2. 当前项目必须显式授权 `program.adopt`。它默认关闭，是专用语言权限；`tool("program.adopt",...)` 和模型工具列表不能绕过这一接口。
3. 宿主重新执行给定测试（包含 fixtures），不接受外部伪造的 `{passed:true}` 报告。
4. 检查基准版本和源码哈希，在同一事务内更新源码、文件、入口及语言版本，并记录 `program.adopted` 与既有 `graph.saved`。

候选不能改变项目名称、授权、设置或解释器 Python 文件。相同候选的空修改被拒绝；版本过期、测试失败、缺少授权或基准不匹配不会写入。当前 run 保持原快照，同一基准再次采用会冲突；下次显式运行读取新版本，不自动重启 Engine，也不偷偷改写正在运行的闭包。

提交前观察到的取消阻止提交。提交完成后收到取消可以让 run 结束为取消，但不能回滚已提交版本；事务中的采用事件是持久事实。它与工具已发生的外部效果一样，需要程序显式处理。

## 一个用本语言给自己编程的 dot

完整源码随发行包提供：[main.chaos](../examples/self-programming-dot/main.chaos)、[behavior.chaos](../examples/self-programming-dot/behavior.chaos)、[tests.chaos](../examples/self-programming-dot/tests.chaos)。

主程序的演化分支是：

```chaos
let baseline=current_program();
let candidate=program_from(proposed_definition);
let report=await program_probe(candidate,cases);
if !report.passed {throw report}else{null};
let preview=await candidate({mode:"run",value:40});
let receipt=await program_adopt(baseline,candidate,cases,reason="通过指定测试后采用");
```

示例中的 `programmer` 是普通 Agent 声明，使用系统现有模型设置与 planner 路由，没有应用端专用机器人实现。它收到自身定义与修改请求，返回完整 Program JSON；文本响应显式 `parse_json`，不会剥离 Markdown 或猜测代码片段。测试用例由调用方单独提供，候选没有通过返回成功字符串跳过实际测试的通道。

```python
from pathlib import Path
from chaos import Framework
folder=Path("examples/self-programming-dot")
files={p.name:p.read_text() for p in folder.glob("*.chaos")}
with Framework("dot.sqlite3") as app:
    project=app.create_project("dot",source=files["main.chaos"],files=files,
                               capabilities=["program.adopt"])
    result=app.start("dot",project["version"],{
        "mode":"evolve","value":40,
        "request":"把 behavior.answer 改为输入加二，保留其余模块协议",
        "cases":[{"entry":"tests.chaos","input":40,"expect":42},
                 {"entry":"tests.chaos","input":0,"expect":2}]
    },mode="source").result()
```

这段真实模型用法要求先配置模型；文档检查不会发出模型请求。回归测试注册了确定性 Provider，实际运行完整 Chaos 示例连续采用两版行为，验证当前 run 仍调用旧函数、候选调用和下一次 run 使用新函数。它验证语言与版本语义，不代表外部模型在开放任务上的优化成功率。

## 图形界面与无界面使用

IDE 的显式 Program 候选预览采用相同 schema、验证器、测试与提交接口，展示逐文件修改、基准版本及候选哈希。网页是源程序的一种编辑视图；无需网页也能执行上面的 dot。

验证无效、远端版本变化或迟到的异步响应不会覆盖当前草稿。采用使用现有项目 CAS 与 SSE，已开始的运行仍标记自己的版本。未采用的候选表单只保存在页面内存，需导出或采用才能保留；不新增候选数据库；0.26 的持久程序历史见下节，任意程序表单反编译仍未实现。

Framework／Client 共享：

- `validate_program(definition,*,name="saved_program")`：返回规范定义、哈希、导出源码与包摘要。
- `program_schema()`：完整 Program 定义 schema。
- `program_snapshot(project_id)`：当前已保存 `{definition,definitionHash,version}`。
- `adopt_program(project_id,version,definition,*,expected_hash,cases,reason="")`：相同授权、实际测试和 CAS，返回 `{project,receipt}`。

HTTP 使用 `/api/v1/programs/schema`、`/api/v1/programs/validate`、`/api/v1/projects/{id}/program-snapshot`、`/api/v1/projects/{id}/program-adopt`。原 `/program` 语法树接口保持原有用法。

## 执行与规模边界

闭合程序最多 64 个模块、单模块 100000 字符、源码合计 1 MB；普通语言 JSON/字符串输出仍受 250000 字符等既有预算限制。Program 是不透明值，能够携带较大包，不意味着能把整个包作为无限 JSON 输出。结构编辑和逐文件反射仍各自受限。

单模块构造器导出可能无法容纳一个合法的大包。此时 `program_source` 报资源错误；宿主 `validate_program` 仍返回验证成功的定义和摘要，`source:null` 与 `sourceError` 说明导出上限。可继续使用定义 JSON 或正常多模块包；不截断源码。

编译与宏展开、候选运行和纯测试计入共享预算，取消可传递。为了不把自修改错误地录制成可重放技能，本轮在技能录制期间拒绝 Program 执行、测试和采用，并使该次录制不能封存。原 Agent/Team 工具录制不变。

当前调试器可停在父程序调用/等待处，子程序通过独立调用来源事件观察，尚不支持跨解释器单步进入。此机制改变 Chaos 程序、Agent/Team 组合及用户宏模块，不会修改宿主解释器、模型权重或绕过版本发布；语言内核尚未实现自举。自治策略、停止条件和评价标准由程序明确编写。

## 0.23：自动继续下一轮

[Process](PROCESSES.md)可在下一轮重新加载已采用的项目源码，并保留显式 JSON 状态。它没有修改本轮函数或复活调用栈，而是由 Engine 创建新的版本快照；连续自编程无需应用端另写调度循环。源码采用与随后状态提交是两个事务，后者失败时须明确协调已发生效果。

## 0.24 持续角色

[AgentSession](SESSIONS.md)让程序修改与模型上下文同时延续。每轮加载最新源码及持久角色会话；绑定变化须显式分叉，新版本不会悄悄替换既有角色身份。测试和采用仍由普通函数明确执行。


## 0.25：自我迭代的环境证据

函数式不自动等于自修改；这里通过 Program 值、候选纯执行、版本化采用、Process 轮次和 AgentSession 持久角色实现。新增[Computer/Observation](COMPUTERS.md)让角色得到当前运行的真实视觉证据并显式构造行动。图片本身不授予电脑控制权；纯 program_probe 不执行浏览器动作。立即执行的 program_adopt、Session 和外部动作保留各自提交边界。0.45 的 process_evolve 可把源码采用与 Process 检查点纳入同一事务；外部动作失败仍须依据真实回执协调，不宣称跨外部世界事务回滚。下一轮重新取得电脑租约，采用新源码；具名会话可继续同一服务保留的页面，解释器自举和活栈迁移仍未实现。


## 能观察并改写自身策略的 dot

发行包增加 [observed-dot 启动器](../examples/observed-dot/main.chaos)、[持续函数](../examples/observed-dot/dot.chaos)、[行为策略](../examples/observed-dot/behavior.chaos)和[纯验收入口](../examples/observed-dot/tests.chaos)。输入是受控演示页面的 url；页面应有 Advance 1/2/3 三个按钮，点击后分别显示 count:1/2/3。项目需 process.manage、program.adopt、session.manage 和 browser.control。此页面约定仅属于示例应用，运行时不识别这些词。

每轮先真实观察，把图像和当前 Program 交给持续编程角色；角色提出完整程序定义。语言执行纯策略测试后采用候选，但当前词法函数仍使用旧版本，真实点击并验证页面。此旧示例使用 computer_open，下一轮重新打开资源、重新观察，再加载已采用的新策略；它不证明页面状态跨轮保留。使用 computer_connect(key) 可保留同一服务中的页面，重启恢复边界见[Computer](COMPUTERS.md)。测试连续三个 Process 轮次、两次实际源码采用及三次实际浏览器行动，同时核实持久角色的原生图像历史。这个示例展示语言级机制，并不证明模型生成的任意修订都能提升性能；其纯测试也不提供恶意候选的安全隔离。

## 0.26：取回历史程序并验证恢复

新增 `program_history(after=0,limit=50)` 和 `program_revision(version)` 冷任务；后者得到可反射、编辑、纯测试和调用的历史 Program。`program_adopt(current_program(),historical,cases)` 会重测并生成新版本，旧 origin 不能充当不匹配当前运行的 base。全部编辑入口共用事务快照，界面只投影同一历史；完整语义、迁移范围和恢复 dot 见[程序历史](PROGRAM_HISTORY.md)。

## 0.45 源码与下一轮状态共同提交

[ProcessEvolution](PROCESS_EVOLUTION.md)让持续函数返回不可伪造的演进决定，宿主完成清理与候选验收后，原子提交程序历史、源码、状态、输出、消息确认和运行终态。立即 `program_adopt` 保持原语义；新的[evolving-dot](../examples/evolving-dot/dot.chaos)展示语言内多角色编程及跨重启的版本衔接。


## 语义重构（0.48）

`program_references` 返回同一闭合包的符号证据，`program_rename` 产生保留原 Program 的新候选，保留方言及可信来源；编程智能体无需靠文本替换同时修改声明和消费者。候选仍需实际测试并通过原有 CAS 采用。引用不完整或名称捕获会明确拒绝重命名，见[符号重构](SYMBOL_REFACTORING.md)。

## 采用前的编译分析

`program_compile(candidate)` 可读取候选的依赖身份和保守结构效果。编译成功不保证运行成功、纯度或性能改善，候选仍需现有 probe／adopt 测试与 CAS。所有权、共享预算、工具授权和旧版本接口保持生效，见[增量编译](COMPILER.md)。


## 0.78 原调度器中的模块构造

`module_from(program, bindings={})` 对原生 Program 的封闭包创建独立模块实例，在当前 Evaluator、逻辑任务和效果权限内执行原 AST，返回与 import 一致的导出 Record。显式 bindings 浅复制名称关联、保留活函数／类／智能体的身份；不隐式捕获调用者局部变量。每次构造有独立词法单元和依赖缓存，同实例的菱形导入仅初始化一次，标准模块也不借用外层缓存。宏在原 ProgramBundle 中纯展开，运行时不会导出宏。顶层 await、四类函数和原 AgentTool／Team／群体回调可组合。

代码不是宿主 eval，也不是 run_program 的 JSON 后台任务。构造失败不返回完整 namespace，不自动重试，更不回滚已完成的外部效果。模块返回值中的冷 Task／Generator 不被额外启动。模块及导出函数中的 current_program 返回该封闭定义且 origin 为 null；项目采用和持久演进仍需原 Program／Process 合同。相同定义的 data 标签按源码身份匹配，Class、词法状态和缓存按实例独立。

原生成源码表继续保留四字段。模块全图先通过原64源／1000000字符配额的批量预检，再执行初始化与默认值；不能覆盖固定项目文件。额外 generatedSourceContexts 是纯逻辑文件／虚拟文件映射和版本数据，IDE 在只读源视图中分析完整自有文件包，标准库由原解析器解析，不写项目、快照或编辑草稿。受信自定义 source_handler 的外部副作用不承诺回滚。

这不代表完整外语兼容、reader 宏、通用跨边界 continuation、活栈迁移、自举、AOT 或自研 GC 已完成。
